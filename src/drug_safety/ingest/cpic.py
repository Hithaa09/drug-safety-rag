"""CPIC guideline recommendations for the v1 drugs and genes: tier-1 guideline evidence (spec:
"CPIC clopidogrel guideline chunk").

Usage:
    python -m drug_safety.ingest.cpic fetch    # network: 4 requests to the CPIC API
    python -m drug_safety.ingest.cpic parse    # raw responses -> documents.json (offline)

Source: the CPIC database API (https://api.cpicpgx.org/v1/, PostgREST; docs at
github.com/cpicpgx/cpic-data/wiki). One document per CPIC guideline; each kept recommendation row
(drug x phenotype: implication, recommendation, strength) becomes one chunk in
rag/chunk_cpic.py.

Licence (checked 2026-11-11, github.com/cpicpgx/cpic-data LICENSE.md, last changed 2026-03-09):
"All curated content published by CPIC is available free of restriction under the CC0 1.0
Universal Public Domain Dedication". CPIC asks for attribution (CPIC®, URL, date accessed,
version) and forbids reusing its logo; both are honoured in LICENSE below and in each document.

Kept: rows for a v1 drug where at least one v1 gene has a non-normal phenotype. Dropped:
rows where every v1 gene is normal or indeterminate ("Initiate therapy with recommended starting
dose"): the rule engine only flags abnormal phenotypes, so those rows could only be wrong
evidence for a flag. Rows that differ only in activity score are merged (CPIC repeats the same
text per score; amitriptyline has 206 rows).

Writes:
    data/raw/cpic/<table>.json             untouched API responses (+ _fetched_at)
    data/processed/cpic/documents.json     one CpicDocument per guideline
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.ingest.dailymed import USER_AGENT
from drug_safety.scope import load_scope

log = logging.getLogger(__name__)

API = "https://api.cpicpgx.org/v1/"
SOURCE = "cpic"
LICENSE = (
    "CC0 1.0 (CPIC curated content). Attribution requested: CPIC®, www.clinpgx.org, "
    "content subject to updates"
)
US_NAME = {"paracetamol": "acetaminophen"}  # v1 name -> CPIC (RxNorm) name
TABLES = ("drug", "pair", "guideline", "recommendation")
# No action: a normal or unknown phenotype, or a negative / missing allele result (HLA-B).
_NOT_ACTIONABLE = re.compile(
    r"^(?:normal|indeterminate|n/a|no result|unknown)\b|\bnegative$|^no\b.*\bresult$", re.I
)


class CpicRecommendation(BaseModel):
    ids: list[int]  # CPIC recommendation row ids merged into this one (same text)
    drug: str  # v1 name
    phenotypes: dict[str, str]  # gene -> phenotype (only genes the row names)
    implications: dict[str, str]
    recommendation: str
    classification: str  # strength: Strong, Moderate, Optional, No Recommendation
    population: str | None = None
    comments: str | None = None


class CpicDocument(BaseModel):
    doc_id: str  # cpic:<ClinPGx guideline id>
    source: str = SOURCE
    external_id: str
    title: str
    url: str
    drugs: list[str]
    genes: list[str]
    study_type: str = "guideline"
    tier: int = 1
    pub_year: int | None = None
    license: str = LICENSE
    raw_path: str
    source_version: str  # CPIC guideline version
    retrieved_at: str
    load_id: str
    recommendations: list[CpicRecommendation]


def raw_path(settings: Settings, table: str) -> Path:
    return settings.raw_dir / SOURCE / f"{table}.json"


def out_path(settings: Settings) -> Path:
    return settings.data_dir / "processed" / SOURCE / "documents.json"


def v1_names() -> dict[str, str]:
    """CPIC drug name -> v1 name."""
    return {US_NAME.get(d, d): d for d in load_scope().drugs}


def fetch(settings: Settings) -> dict[str, int]:
    http = httpx.Client(base_url=API, timeout=60, headers={"User-Agent": USER_AGENT})

    def get(path, **params):
        r = http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    drugs = get("drug", select="drugid,name,rxnormid,clinpgxid,guidelineid")
    ids = [d["drugid"] for d in drugs if d["name"].lower() in v1_names()]
    inlist = f"in.({','.join(ids)})"
    bodies = {
        "drug": drugs,
        "pair": get("pair", drugid=inlist),
        "guideline": get("guideline"),
        "recommendation": get("recommendation", drugid=inlist),
    }
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    for table, rows in bodies.items():
        p = raw_path(settings, table)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"_fetched_at": stamp, "_url": API + table, "rows": rows}),
                     encoding="utf-8")  # fmt: skip
    return {t: len(b) for t, b in bodies.items()}


def read_raw(settings: Settings, table: str) -> dict:
    return json.loads(raw_path(settings, table).read_text())


def actionable(phenotypes: dict[str, str], genes: set[str]) -> bool:
    """At least one v1 gene in the row has a phenotype the engine could flag."""
    return any(g in genes and v and not _NOT_ACTIONABLE.search(v) for g, v in phenotypes.items())


def _clean(v) -> str | None:
    if not isinstance(v, str):
        return None
    v = v.strip()
    return None if not v or v.lower() in ("n/a", "none") else v


def parse(settings: Settings) -> list[CpicDocument]:
    genes = set(load_scope().genes)
    names = v1_names()
    drug_name = {d["drugid"]: d["name"].lower() for d in read_raw(settings, "drug")["rows"]}
    guidelines = {g["id"]: g for g in read_raw(settings, "guideline")["rows"]}
    raw = read_raw(settings, "recommendation")
    load_id = f"{SOURCE}-{datetime.now(UTC):%Y%m%dT%H%M%S}"

    merged: dict[tuple, CpicRecommendation] = {}
    for r in sorted(raw["rows"], key=lambda r: r["id"]):
        phen = {g: v for g, v in (r.get("phenotypes") or {}).items() if v}
        # HLA-B has no metabolizer phenotype: CPIC gives an allele result ("HLA-B*58:01
        # positive") in `allelestatus`. Used where a gene has no phenotype (Person A, E3/A10).
        for g, v in (r.get("allelestatus") or {}).items():
            if v and g not in phen:
                phen[g] = v
        if not actionable(phen, genes):
            continue
        drug = names[drug_name[r["drugid"]]]
        rec = CpicRecommendation(
            ids=[r["id"]],
            drug=drug,
            phenotypes=phen,
            implications={g: v for g, v in (r.get("implications") or {}).items() if _clean(v)},
            recommendation=r["drugrecommendation"].strip(),
            classification=r.get("classification") or "",
            population=_clean(r.get("population")),
            comments=_clean(r.get("comments")),
        )
        key = (r["guidelineid"], drug, json.dumps(rec.model_dump(exclude={"ids"}), sort_keys=True))
        if key in merged:
            merged[key].ids.append(r["id"])
        else:
            merged[key] = rec

    docs = []
    for gid in sorted({k[0] for k in merged}):
        g = guidelines[gid]
        recs = [v for k, v in merged.items() if k[0] == gid]
        docs.append(
            CpicDocument(
                doc_id=f"{SOURCE}:{g['clinpgxid']}",
                external_id=g["clinpgxid"],
                title=f"CPIC guideline: {g['name']}",
                url=g["url"],
                drugs=sorted({r.drug for r in recs}),
                genes=sorted(g.get("genes") or []),
                raw_path=str(raw_path(settings, "recommendation")),
                source_version=f"CPIC guideline version {g['version']}",
                retrieved_at=raw["_fetched_at"],
                load_id=load_id,
                recommendations=recs,
            )
        )
    p = out_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([d.model_dump() for d in docs], indent=1, ensure_ascii=False),
                 encoding="utf-8")  # fmt: skip
    return docs


def load_cpic_docs(settings: Settings) -> list[CpicDocument]:
    return [CpicDocument.model_validate(d) for d in json.loads(out_path(settings).read_text())]


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m drug_safety.ingest.cpic")
    p.add_argument("cmd", choices=["fetch", "parse"])
    args = p.parse_args(argv)
    if args.cmd == "fetch":
        print("rows fetched:", fetch(settings))
    docs = parse(settings)
    n = sum(len(d.recommendations) for d in docs)
    print(f"{len(docs)} guidelines, {n} recommendations kept -> {out_path(settings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
