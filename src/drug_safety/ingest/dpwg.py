"""DPWG guideline recommendations, via ClinPGx, for the gene-drug pairs CPIC does not cover
(Person A, question E5, 2026-11-14).

Usage:
    python -m drug_safety.ingest.dpwg fetch    # network: 1 download from the ClinPGx API
    python -m drug_safety.ingest.dpwg parse    # raw zip -> documents.json (offline)

Source: ClinPGx's guideline annotations download
(https://api.clinpgx.org/v1/download/file/data/guidelineAnnotations.json.zip). Each DPWG
annotation has a table "Allele/Genotype/Phenotype | Drug | Description | Recommendation" whose
wording ClinPGx takes from the Dutch guidelines.

Licence (LICENSE.txt inside the download, kept with the raw file): CC BY-SA 4.0, plus ClinPGx's
terms: changed data passed on must stay under the same licence, and "Under no circumstances can
ClinPGx data be sold for other's private or commercial use". Accepted by Person A (E1, E5): the
project stays non-commercial.

Kept, per row: a v1 drug and v1 gene that CPIC has no recommendation for (CPIC chunks are read to
decide), a phenotype row (PM, IM, UM) or a VKORC1 -1639 genotype, and a recommendation other
than "no action". Dropped: "NO action is needed" rows (they could only be wrong evidence for a
flag), and star-allele genotype rows (CYP2C9*1/*3) that repeat their phenotype row's advice.
Found 2026-11-14: acenocoumarol-CYP2C9 and clozapine-CYP2D6 are "no action" in DPWG, so only
acenocoumarol-VKORC1, warfarin-CYP2C9 and warfarin-VKORC1 give chunks.

VKORC1: ClinPGx writes the same genotype as "rs9923231 AA" in a table and "rs9923231 TT (-1639
AA)" in its summary, for the same advice. Both are stored as "-1639 AA" (the clinical notation),
with the original label kept in the chunk.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.ingest.dailymed import USER_AGENT
from drug_safety.scope import load_scope

URL = "https://api.clinpgx.org/v1/download/file/data/guidelineAnnotations.json.zip"
SOURCE = "dpwg"
LICENSE = (
    "CC BY-SA 4.0 (ClinPGx annotation of the DPWG guideline); no sale for private or commercial "
    "use; credit ClinPGx, www.clinpgx.org"
)
US_NAME = {"paracetamol": "acetaminophen"}
_PHENOTYPE = {"PM": "Poor Metabolizer", "IM": "Intermediate Metabolizer",
              "UM": "Ultrarapid Metabolizer", "NM": "Normal Metabolizer"}  # fmt: skip
_NO_ACTION = re.compile(r"\bno action is (?:needed|required)\b", re.I)


class DpwgRecommendation(BaseModel):
    drug: str  # v1 name
    gene: str
    phenotype: str  # "Poor Metabolizer", "-1639 AA"
    label: str  # the row label exactly as DPWG/ClinPGx writes it
    description: str
    recommendation: str


class DpwgDocument(BaseModel):
    doc_id: str  # dpwg:<ClinPGx annotation id>
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
    source_version: str
    retrieved_at: str
    load_id: str
    recommendations: list[DpwgRecommendation]


def raw_path(settings: Settings) -> Path:
    return settings.raw_dir / "clinpgx" / "guidelineAnnotations.json.zip"


def out_path(settings: Settings) -> Path:
    return settings.data_dir / "processed" / SOURCE / "documents.json"


def fetch(settings: Settings) -> int:
    r = httpx.get(URL, timeout=120, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    p = raw_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(r.content)
    (p.parent / "fetched_at.txt").write_text(datetime.now(UTC).isoformat(timespec="seconds"))
    return len(r.content)


def _cells(row_html: str) -> list[str]:
    cells = re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row_html, re.S)
    return [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip() for c in cells]


def guideline_rows(text_html: str) -> list[list[str]]:
    """Rows of the table headed 'Allele/Genotype/Phenotype | Drug | Description |
    Recommendation' (the current guideline; older tables in the same text are skipped)."""
    for table in re.findall(r"<table.*?</table>", text_html, re.S):
        rows = [_cells(r) for r in re.findall(r"<tr.*?</tr>", table, re.S)]
        if rows and rows[0][:1] == ["Allele/Genotype/Phenotype"] and len(rows[0]) >= 4:
            return [r for r in rows[1:] if len(r) >= 4]
    return []


def phenotype(label: str, gene: str) -> str | None:
    """The row's phenotype in the vocabulary flags use, or None for a row to skip."""
    rest = label.removeprefix(gene).strip()
    if gene == "VKORC1":
        m = re.search(r"(?:rs9923231|-1639)\s+([ACGT]{2})\b", rest)
        return f"-1639 {m.group(1)}" if m else None
    if rest.startswith("*"):  # exact genotype: repeats its phenotype row
        return None
    m = re.match(r"(PM|IM|UM|NM)\b", rest)
    return _PHENOTYPE[m.group(1)] if m else None


def covered_by_cpic(settings: Settings) -> set[tuple[str, str]]:
    from drug_safety.rag.chunk_cpic import read_cpic_chunks

    return {(c.drugs[0], g) for c in read_cpic_chunks(settings) for g in c.genes}


def parse(settings: Settings) -> tuple[list[DpwgDocument], dict]:
    scope = load_scope()
    drugs = {US_NAME.get(d, d): d for d in scope.drugs}
    genes = set(scope.genes)
    cpic = covered_by_cpic(settings)
    zp = raw_path(settings)
    fetched = (zp.parent / "fetched_at.txt").read_text().strip()
    load_id = f"{SOURCE}-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    docs, report = [], {}
    with zipfile.ZipFile(zp) as z:
        created = next((n for n in z.namelist() if n.startswith("CREATED_")), "")
        for name in sorted(n for n in z.namelist() if n.endswith(".json")):
            g = json.loads(z.read(name))["guideline"]
            if g.get("source") != "DPWG":
                continue
            ds = [drugs[c["name"]] for c in g.get("relatedChemicals", []) if c["name"] in drugs]
            gs = [x["symbol"] for x in g.get("relatedGenes", []) if x["symbol"] in genes]
            pairs = [(d, ge) for d in ds for ge in gs if (d, ge) not in cpic]
            if not pairs:
                continue
            recs = []
            for label, drug_cell, desc, rec, *_ in guideline_rows(g["textMarkdown"]["html"]):
                gene = next((ge for _, ge in pairs if label.startswith(ge)), None)
                drug = drugs.get(drug_cell.lower())
                if not gene or (drug, gene) not in pairs or _NO_ACTION.search(rec):
                    continue
                if ph := phenotype(label, gene):
                    recs.append(DpwgRecommendation(drug=drug, gene=gene, phenotype=ph,
                                                   label=label, description=desc,
                                                   recommendation=rec))  # fmt: skip
            for p in pairs:
                report[f"{p[0]} x {p[1]}"] = sum((r.drug, r.gene) == p for r in recs)
            if recs:
                docs.append(DpwgDocument(
                    doc_id=f"{SOURCE}:{g['id']}", external_id=g["id"],
                    title=g["name"].replace("Annotation of DPWG Clinical Guideline for",
                                            "DPWG guideline (ClinPGx annotation):"),
                    url=f"https://www.clinpgx.org/guidelineAnnotation/{g['id']}",
                    drugs=sorted({r.drug for r in recs}), genes=sorted({r.gene for r in recs}),
                    raw_path=str(zp), retrieved_at=fetched, load_id=load_id,
                    source_version=f"ClinPGx annotation version {g['version']}; {created}",
                    recommendations=recs,
                ))  # fmt: skip
    p = out_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([d.model_dump() for d in docs], indent=1, ensure_ascii=False))
    return docs, report


def load_dpwg_docs(settings: Settings) -> list[DpwgDocument]:
    return [DpwgDocument.model_validate(d) for d in json.loads(out_path(settings).read_text())]


def license_text(settings: Settings) -> str:
    with zipfile.ZipFile(raw_path(settings)) as z:
        return z.read("LICENSE.txt").decode()


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    p = argparse.ArgumentParser(prog="python -m drug_safety.ingest.dpwg")
    p.add_argument("cmd", choices=["fetch", "parse"])
    args = p.parse_args(argv)
    if args.cmd == "fetch":
        print(f"downloaded {fetch(settings):,} bytes -> {raw_path(settings)}")
    docs, report = parse(settings)
    print("pairs CPIC does not cover, recommendations kept:")
    for pair, n in sorted(report.items()):
        print(f"  {pair:30} {n}")
    print(f"{len(docs)} documents -> {out_path(settings)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
