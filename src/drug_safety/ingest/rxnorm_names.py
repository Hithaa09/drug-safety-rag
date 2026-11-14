"""Every prescribable drug name (RxNorm), for the LLM validator's "no extra drug" check.

Usage:
    python -m drug_safety.ingest.rxnorm_names fetch    # network: 2 requests to NLM RxNav
    python -m drug_safety.ingest.rxnorm_names verify   # offline acceptance checks

Why: spec validator rule 4 rejects an LLM answer that names a drug which is neither prescribed
nor in a flag. Our alias list (scope/drug_aliases.yaml) knows only the 25 v1 drugs, so it could
not see Qwen naming, say, prasugrel. This list adds every ingredient (IN) and brand (BN) in
NLM's Prescribable RxNorm subset, minus the ordinary words in scope/drug_name_ignore.yaml.

Source: RxNav Prescribable API (https://lhncbc.nlm.nih.gov/RxNav/APIs/PrescribableAPIs.html),
RxNorm names created by NLM (free; attribution below).

Writes:
    data/raw/rxnorm/prescribe_<IN|BN>.json   untouched API responses
    data/processed/drug_names.json           {ingredients: [...], brands: [...], ...}
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.ingest.dailymed import USER_AGENT
from drug_safety.ingest.rxclass import ATTRIBUTION
from drug_safety.llm.drug_names import names_path
from drug_safety.scope import load_scope

log = logging.getLogger(__name__)

URL = "https://rxnav.nlm.nih.gov/REST/Prescribe/allconcepts.json"
IGNORE_FILE = Path("scope/drug_name_ignore.yaml")
MAX_LEN = 40  # longer names are chemical descriptions no explanation will contain
US_NAME = {"paracetamol": "acetaminophen"}


def raw_path(settings: Settings, tty: str) -> Path:
    return settings.raw_dir / "rxnorm" / f"prescribe_{tty}.json"


def out_path(settings: Settings) -> Path:
    return names_path(settings)  # the LLM validator reads it from there


def ignored(path: Path = IGNORE_FILE) -> set[str]:
    body = yaml.safe_load(path.read_text(encoding="utf-8"))["ignore"]
    return {n.lower() for names in body.values() for n in names}


def usable(name: str) -> bool:
    return len(name) <= MAX_LEN and not any(c in name for c in "(),;[]")


def build(settings: Settings) -> dict:
    skip = ignored()
    lists = {}
    for tty, key in (("IN", "ingredients"), ("BN", "brands")):
        body = json.loads(raw_path(settings, tty).read_text())
        names = {c["name"].lower() for c in body["minConceptGroup"]["minConcept"]}
        lists[key] = sorted(n for n in names if usable(n) and n not in skip)
    out = {
        "source": URL,
        "fetched_at": json.loads(raw_path(settings, "IN").read_text()).get("_fetched_at"),
        "attribution": ATTRIBUTION,
        "ignore_file": str(IGNORE_FILE),
        **lists,
    }
    out_path(settings).parent.mkdir(parents=True, exist_ok=True)
    out_path(settings).write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def run_fetch(settings: Settings) -> dict:
    http = httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT})
    for tty in ("IN", "BN"):
        r = http.get(URL, params={"tty": tty})
        r.raise_for_status()
        body = r.json()
        body["_fetched_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        raw_path(settings, tty).parent.mkdir(parents=True, exist_ok=True)
        raw_path(settings, tty).write_text(json.dumps(body), encoding="utf-8")
    return build(settings)


def read_names(settings: Settings) -> dict:
    return json.loads(out_path(settings).read_text())


# ---------------------------------------------------------------- verify


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


# Drugs outside v1 that an explanation could plausibly name (alternatives in the CPIC and label
# text): the check exists to catch exactly these.
MUST_DETECT = {
    "ingredients": ["prasugrel", "ticagrelor", "esomeprazole", "morphine", "heparin", "lithium"],
    "brands": ["effient", "brilinta", "nexium", "plavix", "demerol"],
}
# Ordinary words, lab analytes and lifestyle factors that explanations use all the time.
MUST_NOT_DETECT = ["water", "iron", "glucose", "creatinine", "sodium", "potassium", "ethanol",
                   "nicotine", "vitamin k", "align", "react", "perform"]  # fmt: skip


def verify(settings: Settings) -> list[Check]:
    checks: list[Check] = []

    def add(name, ok, detail=""):
        checks.append(Check(name=name, ok=ok, detail="" if ok else detail))

    if not out_path(settings).exists():
        add("drug name list exists", False, "run `make fetch-drug-names`")
        return checks
    d = read_names(settings)
    ins, bns = set(d["ingredients"]), set(d["brands"])
    add("over 4,000 ingredient names", len(ins) > 4000, f"{len(ins)}")
    add("over 3,000 brand names", len(bns) > 3000, f"{len(bns)}")
    v1 = [US_NAME.get(x, x) for x in load_scope().drugs if x != "acenocoumarol"]  # not US
    add("every US-marketed v1 drug is a listed ingredient", set(v1) <= ins, str(set(v1) - ins))
    for key, names in MUST_DETECT.items():
        have = ins if key == "ingredients" else bns
        add(f"{key} include {', '.join(names)}", set(names) <= have, str(set(names) - have))
    leaked = [w for w in MUST_NOT_DETECT if w in ins | bns]
    add("ordinary words and lab terms are not listed", not leaked, str(leaked))
    add(
        "every ignore entry exists in the raw lists (no typos)",
        not (stale := sorted(ignored() - raw_names(settings))),
        f"not in RxNorm: {stale[:10]}",
    )
    short = sorted(n for n in ins | bns if len(n) < 3)
    add("no names shorter than 3 characters", not short, str(short))
    add("attribution recorded", "National Library of Medicine" in d.get("attribution", ""))
    return checks


def raw_names(settings: Settings) -> set[str]:
    out = set()
    for tty in ("IN", "BN"):
        body = json.loads(raw_path(settings, tty).read_text())
        out |= {c["name"].lower() for c in body["minConceptGroup"]["minConcept"]}
    return out


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m drug_safety.ingest.rxnorm_names")
    p.add_argument("cmd", choices=["fetch", "verify"])
    args = p.parse_args(argv)
    if args.cmd == "fetch":
        d = run_fetch(settings)
        print(f"{len(d['ingredients'])} ingredient and {len(d['brands'])} brand names written")
        return 0
    checks = verify(settings)
    failed = [c for c in checks if not c.ok]
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name}" + (f": {c.detail}" if c.detail else ""))
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed.")
    print("RESULT:", "PASS" if not failed else "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
