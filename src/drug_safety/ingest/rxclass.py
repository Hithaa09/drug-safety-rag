"""Drug classes for the v1 drugs, from NLM RxClass.

Usage:
    python -m drug_safety.ingest.rxclass fetch    # network: classes per drug
    python -m drug_safety.ingest.rxclass verify   # offline acceptance checks

Source: RxClass (https://rxnav.nlm.nih.gov), FDA Established Pharmacologic Class (FDASPL,
has_epc) and MED-RT mechanism of action (has_moa), matched on the plain ingredient.
openFDA's class fields were tried first and rejected: wrong for several drugs.

Writes:
    data/raw/rxclass/<drug>.json             untouched RxClass responses
    data/processed/drug_classes.json         {drug: {epc, moa, classes, source}}
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.ingest.dailymed import USER_AGENT
from drug_safety.scope import load_scope

log = logging.getLogger(__name__)

URL = "https://rxnav.nlm.nih.gov/REST/rxclass/class/byDrugName.json"
CLASSES_FILE = Path("scope/drug_classes.yaml")
US_NAME = {"paracetamol": "acetaminophen"}
ATTRIBUTION = (
    "This product uses publicly available data from the U.S. National Library of Medicine "
    "(NLM), National Institutes of Health, Department of Health and Human Services; NLM is not "
    "responsible for the product and does not endorse or recommend this or any other product."
)


class DrugClasses(BaseModel):
    drug: str
    epc: list[str]
    moa: list[str]
    classes: list[str]  # keys from scope/drug_classes.yaml
    source: str  # "RxClass" or "manual: <reason>"
    retrieved_at: str


@lru_cache
def class_definitions(path: Path = CLASSES_FILE) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def assign_classes(epc: list[str], moa: list[str], defs: dict) -> list[str]:
    return [
        key
        for key, c in defs["classes"].items()
        if set(c.get("epc", [])) & set(epc) or set(c.get("moa", [])) & set(moa)
    ]


def fetch_one(drug: str, http: httpx.Client, settings: Settings) -> DrugClasses:
    name = US_NAME.get(drug, drug)
    found: dict[str, list[str]] = {}
    raw = {}
    for rela, src in (("has_epc", "FDASPL"), ("has_moa", "MEDRT")):
        for attempt in range(4):
            time.sleep(0.1)  # RxNav allows 20 requests/s per IP
            r = http.get(URL, params={"drugName": name, "relaSource": src, "relas": rela})
            if r.status_code == 200:
                break
            time.sleep(2**attempt)
        r.raise_for_status()
        raw[rela] = r.json()
        items = (raw[rela].get("rxclassDrugInfoList") or {}).get("rxclassDrugInfo", [])
        # Only rows for the plain ingredient (tty IN), not combination products.
        found[rela] = sorted(
            {
                i["rxclassMinConceptItem"]["className"]
                for i in items
                if i["minConcept"].get("tty") == "IN"
            }
        )
    path = settings.raw_dir / "rxclass" / f"{drug}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=1), encoding="utf-8")

    defs = class_definitions()
    now = datetime.now(UTC).isoformat(timespec="seconds")
    manual = defs.get("manual_members", {}).get(drug)
    if manual and not (found["has_epc"] or found["has_moa"]):
        return DrugClasses(
            drug=drug,
            epc=[],
            moa=[],
            classes=manual["classes"],
            source=f"manual: {manual['reason']}",
            retrieved_at=now,
        )
    return DrugClasses(
        drug=drug,
        epc=found["has_epc"],
        moa=found["has_moa"],
        classes=assign_classes(found["has_epc"], found["has_moa"], defs),
        source="RxClass",
        retrieved_at=now,
    )


def out_path(settings: Settings) -> Path:
    return settings.data_dir / "processed" / "drug_classes.json"


def run_fetch(settings: Settings) -> list[DrugClasses]:
    http = httpx.Client(timeout=30, headers={"User-Agent": USER_AGENT})
    rows = [fetch_one(d, http, settings) for d in load_scope().drugs]
    out_path(settings).write_text(
        json.dumps({"attribution": ATTRIBUTION, "drugs": [r.model_dump() for r in rows]}, indent=2),
        encoding="utf-8",
    )
    return rows


def read_classes(settings: Settings) -> dict[str, DrugClasses]:
    body = json.loads(out_path(settings).read_text())
    return {r["drug"]: DrugClasses.model_validate(r) for r in body["drugs"]}


def members(settings: Settings) -> dict[str, list[str]]:
    """class key -> v1 drugs in it."""
    out: dict[str, list[str]] = {k: [] for k in class_definitions()["classes"]}
    for d, row in read_classes(settings).items():
        for k in row.classes:
            out[k].append(d)
    return out


# ---------------------------------------------------------------- verify


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


# Facts any pharmacist would agree with; if RxClass or the class file drifts, this fails.
EXPECTED = {
    "nsaid": {"aspirin", "ibuprofen", "diclofenac", "celecoxib"},
    "ppi": {"omeprazole", "pantoprazole"},
    "statin": {"simvastatin", "atorvastatin", "rosuvastatin"},
    "ssri": {"sertraline", "citalopram"},
    "tca": {"amitriptyline"},
    "opioid": {"codeine", "tramadol"},
    "vka": {"warfarin", "acenocoumarol"},
    "antiplatelet": {"clopidogrel", "aspirin"},
    "ccb": {"amlodipine"},
}
EXPECTED_NO_CLASS = {
    "metformin",
    "phenytoin",
    "clozapine",
    "theophylline",
    "paracetamol",
    "methotrexate",
    "allopurinol",
}


def verify(settings: Settings) -> list[Check]:
    checks: list[Check] = []

    def add(name, ok, detail=""):
        checks.append(Check(name=name, ok=ok, detail="" if ok else detail))

    if not out_path(settings).exists():
        add("classes file exists", False, "run `make fetch-classes`")
        return checks
    rows = read_classes(settings)
    scope = load_scope()
    add(
        "one row per v1 drug",
        set(rows) == set(scope.drugs),
        f"differs: {sorted(set(rows) ^ set(scope.drugs))}",
    )
    m = members(settings)
    for key, want in EXPECTED.items():
        add(
            f"class {key} = {sorted(want)}",
            set(m.get(key, [])) == want,
            f"got {sorted(m.get(key, []))}",
        )
    no_class = {d for d, r in rows.items() if not r.classes}
    add(
        "drugs outside every class are the expected ones",
        no_class == EXPECTED_NO_CLASS,
        f"got {sorted(no_class)}",
    )
    manual = {d for d, r in rows.items() if r.source.startswith("manual")}
    add(
        "manual entries only where RxClass has nothing",
        manual == {"acenocoumarol"},
        f"got {sorted(manual)}",
    )
    raw_missing = [d for d in rows if not (settings.raw_dir / "rxclass" / f"{d}.json").exists()]
    add("raw responses kept", not raw_missing, str(raw_missing))
    return checks


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m drug_safety.ingest.rxclass")
    p.add_argument("cmd", choices=["fetch", "verify"])
    args = p.parse_args(argv)
    if args.cmd == "fetch":
        for r in run_fetch(settings):
            print(f"{r.drug:14} {', '.join(r.classes) or '-':22} EPC: {'; '.join(r.epc) or '-'}")
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
