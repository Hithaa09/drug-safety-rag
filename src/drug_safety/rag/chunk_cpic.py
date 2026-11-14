"""CPIC recommendations -> evidence chunks (one chunk per recommendation; they are short).

Usage:
    python -m drug_safety.rag.chunk_cpic run      # -> data/processed/chunks/cpic.jsonl
    python -m drug_safety.rag.chunk_cpic verify   # offline acceptance checks

A chunk reads like the label and paper chunks: a context line, then the recommendation as CPIC
states it (phenotype, population, implication, recommendation, strength, comments), unchanged.
Tagging: `drugs` is the recommendation's own drug only. CPIC text also names alternatives
("Use prasugrel or ticagrelor"), and other v1 drugs in a comment must not make a clopidogrel
recommendation show up as evidence for, say, an omeprazole flag.
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
from drug_safety.ingest.cpic import (
    LICENSE,
    CpicDocument,
    CpicRecommendation,
    actionable,
    load_cpic_docs,
    read_raw,
    v1_names,
)
from drug_safety.rag.chunking import MAX_TOKENS, Chunk, TokenCounter, bge_token_counter
from drug_safety.scope import load_scope

_PHENOTYPE = re.compile(r"^(\S+) phenotype: (.+)$", re.M)

# Level-A CPIC pairs in v1 that CPIC publishes without recommendation rows, and why.
KNOWN_GAPS = {
    ("warfarin", "CYP2C9"): "CPIC's warfarin guideline is a dosing algorithm, not a table",
    ("warfarin", "VKORC1"): "CPIC's warfarin guideline is a dosing algorithm, not a table",
}


def out_path(settings: Settings) -> Path:
    return settings.data_dir / "processed" / "chunks" / "cpic.jsonl"


def phenotype_line(r: CpicRecommendation) -> str:
    # an allele result already names its gene: "HLA-B*15:02 positive", not "HLA-B HLA-B*..."
    return "; ".join(p if p.startswith(g) else f"{g} {p}" for g, p in r.phenotypes.items())


def heading(r: CpicRecommendation) -> str:
    """'CYP2C19 Poor Metabolizer · CVI ACS PCI': what tells two rows of one guideline apart."""
    return phenotype_line(r) + (f" · {r.population}" if r.population else "")


def chunk_text(doc: CpicDocument, r: CpicRecommendation) -> str:
    genes = " · ".join(r.phenotypes)
    lines = [f"guideline | {r.drug} · {genes} | {doc.title} | {phenotype_line(r)}", ""]
    lines += [f"{g} phenotype: {p}" for g, p in r.phenotypes.items()]
    if r.population:
        lines.append(f"Population: {r.population}")
    lines += [f"Implication ({g}): {v}" for g, v in r.implications.items()]
    lines.append(f"Recommendation: {r.recommendation}")
    if r.classification:
        lines.append(f"Strength of recommendation: {r.classification}")
    if r.comments:
        lines.append(f"Comments: {r.comments}")
    return "\n".join(lines)


def chunk_doc(doc: CpicDocument, count: TokenCounter, load_id: str) -> list[Chunk]:
    out, n = [], Counter()
    for r in doc.recommendations:
        n[r.drug] += 1
        text = chunk_text(doc, r)
        out.append(
            Chunk(
                chunk_id=f"{doc.doc_id}:{r.drug}:{n[r.drug]:03d}",
                doc_id=doc.doc_id,
                source=doc.source,
                tier=doc.tier,
                section=r.drug,
                headings=[[heading(r)]],  # the title is the document's; this tells rows apart
                text=text,
                drugs=[r.drug],
                genes=sorted(r.phenotypes),
                token_count=count(text),
                source_version=doc.source_version,
                license=doc.license,
                load_id=load_id,
            )
        )
    return out


def run(settings: Settings, count: TokenCounter) -> dict:
    load_id = f"chunks-cpic-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    chunks = [c for d in load_cpic_docs(settings) for c in chunk_doc(d, count, load_id)]
    p = out_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(c.model_dump_json() + "\n")
    return {
        "chunks": len(chunks),
        "by_drug": dict(sorted(Counter(c.drugs[0] for c in chunks).items())),
    }


def read_cpic_chunks(settings: Settings) -> list[Chunk]:
    with open(out_path(settings), encoding="utf-8") as f:
        return [Chunk.model_validate_json(line) for line in f if line.strip()]


# ---------------------------------------------------------------- verify


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


# Spot checks against what CPIC publishes (each read on the guideline page, 2026-11-11).
# (drug, gene, phenotype, population or "", phrase). Clopidogrel needs the population: for
# neurovascular indications CPIC says "an alternative P2Y12 inhibitor", not "prasugrel or
# ticagrelor".
SPOT = [
    ("clopidogrel", "CYP2C19", "Poor Metabolizer", "CVI ACS PCI", "prasugrel or ticagrelor"),
    ("clopidogrel", "CYP2C19", "Poor Metabolizer", "NVI", "alternative P2Y12 inhibitor"),
    ("codeine", "CYP2D6", "Ultrarapid Metabolizer", "", "Avoid codeine"),
    ("simvastatin", "SLCO1B1", "Poor Function", "", "simvastatin"),
    ("citalopram", "CYP2C19", "Poor Metabolizer", "", "50% reduction"),
]


def verify(settings: Settings, count: TokenCounter) -> list[Check]:
    checks: list[Check] = []

    def add(name, ok, detail=""):
        checks.append(Check(name=name, ok=bool(ok), detail="" if ok else str(detail)))

    scope = load_scope()
    genes, drugs = set(scope.genes), set(scope.drugs)
    chunks = read_cpic_chunks(settings)
    docs = load_cpic_docs(settings)
    add("chunks exist", chunks, "run `make chunk-cpic`")
    ids = [c.chunk_id for c in chunks]
    add("chunk IDs unique", len(ids) == len(set(ids)))
    n_recs = sum(len(d.recommendations) for d in docs)
    add("one chunk per kept recommendation", len(chunks) == n_recs, f"{len(chunks)} vs {n_recs}")

    # every raw row is either in a chunk or dropped for a stated reason
    raw = read_raw(settings, "recommendation")["rows"]
    kept_ids = {i for d in docs for r in d.recommendations for i in r.ids}
    unexplained = [r["id"] for r in raw if r["id"] not in kept_ids
                   and actionable(r.get("phenotypes") or {}, genes)]  # fmt: skip
    add("every actionable CPIC row is kept (dropped rows are normal or out-of-scope genes)",
        not unexplained, unexplained[:5])  # fmt: skip
    leaked = [c.chunk_id for c in chunks
              if not actionable(dict(_PHENOTYPE.findall(c.text)), genes)]  # fmt: skip
    add("no chunk for a normal or indeterminate phenotype", not leaked, leaked[:3])

    names = v1_names()
    name = {d["drugid"]: names.get(d["name"].lower()) for d in read_raw(settings, "drug")["rows"]}
    level_a = {(name[p["drugid"]], p["genesymbol"]) for p in read_raw(settings, "pair")["rows"]
               if p.get("cpiclevel") == "A" and p["genesymbol"] in genes and name.get(p["drugid"])
               and not p.get("removed")}  # fmt: skip
    covered = {(c.drugs[0], g) for c in chunks for g in c.genes}
    missing = sorted(level_a - covered - set(KNOWN_GAPS))
    add(f"every CPIC level-A pair in v1 has a chunk ({len(level_a)} pairs; known gaps listed)",
        not missing, missing)  # fmt: skip
    add("known gaps are real (no chunks for them)", not (set(KNOWN_GAPS) & covered))

    add(
        "tagged with one v1 drug and its v1 genes",
        all(len(c.drugs) == 1 and c.drugs[0] in drugs and set(c.genes) & genes for c in chunks),
    )
    too_long = [(c.chunk_id, c.token_count) for c in chunks if c.token_count > MAX_TOKENS]
    add(f"within chunk limit ({MAX_TOKENS} tokens)", not too_long, too_long[:3])
    add("token counts match the text", all(count(c.text) == c.token_count for c in chunks))
    add(
        "licence CC0 with attribution on every chunk and document",
        all(c.license == LICENSE for c in chunks) and all(d.license == LICENSE for d in docs),
    )
    add("every document links its guideline page", all(d.url.startswith("https://") for d in docs))

    for drug, gene, phen, pop, phrase in SPOT:
        hit = [c for c in chunks if c.drugs == [drug] and f"{gene} phenotype: {phen}\n" in c.text
               and (not pop or f"Population: {pop}\n" in c.text)]  # fmt: skip
        add(
            f"{drug} {gene} {phen} {pop}: CPIC text says '{phrase}'".replace("  ", " "),
            hit and all(phrase.lower() in c.text.lower() for c in hit),
            f"{len(hit)} chunks",
        )
    return checks


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    p = argparse.ArgumentParser(prog="python -m drug_safety.rag.chunk_cpic")
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
