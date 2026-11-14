"""DPWG recommendations -> evidence chunks (one per recommendation, as for CPIC).

Usage:
    python -m drug_safety.rag.chunk_dpwg run      # -> data/processed/chunks/dpwg.jsonl
    python -m drug_safety.rag.chunk_dpwg verify   # offline acceptance checks

Chunks use the same "<gene> phenotype: <value>" line as CPIC chunks, so the retriever's rule
(a guideline chunk only for a gene flag with the same phenotype, at most one per flag) applies to
both sources unchanged.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.ingest.dpwg import (
    LICENSE,
    DpwgDocument,
    DpwgRecommendation,
    license_text,
    load_dpwg_docs,
)
from drug_safety.rag.chunk_cpic import read_cpic_chunks
from drug_safety.rag.chunking import MAX_TOKENS, Chunk, TokenCounter, bge_token_counter


def out_path(settings: Settings) -> Path:
    return settings.data_dir / "processed" / "chunks" / "dpwg.jsonl"


def chunk_text(doc: DpwgDocument, r: DpwgRecommendation) -> str:
    return "\n".join([
        f"guideline | {r.drug} · {r.gene} | {doc.title} | {r.gene} {r.phenotype}",
        "",
        f"{r.gene} phenotype: {r.phenotype}",
        f"As DPWG writes it: {r.label}",
        f"Description: {r.description}",
        f"Recommendation: {r.recommendation}",
        "Source: Dutch Pharmacogenetics Working Group (DPWG) guideline, as annotated by ClinPGx",
    ])  # fmt: skip


def chunk_doc(doc: DpwgDocument, count: TokenCounter, load_id: str) -> list[Chunk]:
    out, n = [], Counter()
    for r in doc.recommendations:
        n[r.drug] += 1
        text = chunk_text(doc, r)
        out.append(Chunk(
            chunk_id=f"{doc.doc_id}:{r.drug}:{n[r.drug]:03d}", doc_id=doc.doc_id,
            source=doc.source, tier=doc.tier, section=r.drug,
            headings=[[f"{r.gene} {r.phenotype}"]], text=text, drugs=[r.drug], genes=[r.gene],
            token_count=count(text), source_version=doc.source_version, license=doc.license,
            load_id=load_id,
        ))  # fmt: skip
    return out


def run(settings: Settings, count: TokenCounter) -> dict:
    load_id = f"chunks-dpwg-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    chunks = [c for d in load_dpwg_docs(settings) for c in chunk_doc(d, count, load_id)]
    p = out_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(c.model_dump_json() + "\n" for c in chunks), encoding="utf-8")
    return {"chunks": len(chunks), "by_pair": dict(Counter(f"{c.drugs[0]} x {c.genes[0]}"
                                                          for c in chunks))}  # fmt: skip


def read_dpwg_chunks(settings: Settings) -> list[Chunk]:
    with open(out_path(settings), encoding="utf-8") as f:
        return [Chunk.model_validate_json(line) for line in f if line.strip()]


# ---------------------------------------------------------------- verify


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


# What the review found (2026-11-14), checked against the chunks.
EXPECTED_PAIRS = {("acenocoumarol", "VKORC1"), ("warfarin", "CYP2C9"), ("warfarin", "VKORC1")}
SPOT = [  # (drug, gene, phenotype, phrase from DPWG's recommendation)
    ("acenocoumarol", "VKORC1", "-1639 AA", "50% of the standard initial dose"),
    ("warfarin", "VKORC1", "-1639 AA", "60% of the standard initial dose"),
    ("warfarin", "CYP2C9", "Poor Metabolizer", "20% of the standard initial dose"),
    ("warfarin", "CYP2C9", "Intermediate Metabolizer", "65% of the standard initial dose"),
]
_PHENOTYPE = re.compile(r"^(\S+) phenotype: (.+)$", re.M)


def verify(settings: Settings, count: TokenCounter) -> list[Check]:
    checks: list[Check] = []

    def add(name, ok, detail=""):
        checks.append(Check(name=name, ok=bool(ok), detail="" if ok else str(detail)))

    chunks = read_dpwg_chunks(settings)
    docs = load_dpwg_docs(settings)
    lic = license_text(settings)
    add("ClinPGx licence kept with the raw download (CC BY-SA 4.0, no-sale clause)",
        "ShareAlike 4.0" in lic and "sold" in lic, lic[:120])  # fmt: skip
    pairs = {(c.drugs[0], c.genes[0]) for c in chunks}
    add("chunks exactly for the pairs that have DPWG advice and no CPIC recommendation",
        pairs == EXPECTED_PAIRS, sorted(pairs ^ EXPECTED_PAIRS))  # fmt: skip
    cpic = {(c.drugs[0], g) for c in read_cpic_chunks(settings) for g in c.genes}
    add("no pair CPIC already covers", not (pairs & cpic), sorted(pairs & cpic))
    add("one chunk per kept recommendation",
        len(chunks) == sum(len(d.recommendations) for d in docs))  # fmt: skip
    add("chunk IDs unique", len({c.chunk_id for c in chunks}) == len(chunks))
    no_action = [c.chunk_id for c in chunks if re.search(r"no action is", c.text, re.I)]
    add("no 'no action' recommendation kept", not no_action, no_action)
    one_line = all(len(_PHENOTYPE.findall(c.text)) == 1 for c in chunks)
    add("each chunk has exactly one phenotype line, for the retriever's rule", one_line)
    too_long = [(c.chunk_id, c.token_count) for c in chunks if c.token_count > MAX_TOKENS]
    add(f"within chunk limit ({MAX_TOKENS} tokens)", not too_long, too_long)
    add("token counts match the text", all(count(c.text) == c.token_count for c in chunks))
    add("licence and attribution on every chunk", all(c.license == LICENSE for c in chunks))
    for drug, gene, phen, phrase in SPOT:
        hit = [c for c in chunks if c.drugs == [drug] and f"{gene} phenotype: {phen}\n" in c.text]
        add(f"{drug} {gene} {phen}: DPWG says '{phrase}'",
            len(hit) == 1 and phrase in hit[0].text, f"{len(hit)} chunks")  # fmt: skip
    return checks


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    p = argparse.ArgumentParser(prog="python -m drug_safety.rag.chunk_dpwg")
    p.add_argument("cmd", choices=["run", "verify"])
    args = p.parse_args(argv)
    count = bge_token_counter(settings.embedding_model)
    if args.cmd == "run":
        print(json.dumps(run(settings, count), indent=1))
        return 0
    checks = verify(settings, count)
    failed = [c for c in checks if not c.ok]
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name}" + (f": {c.detail}" if c.detail else ""))
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed.")
    print("RESULT:", "PASS" if not failed else "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
