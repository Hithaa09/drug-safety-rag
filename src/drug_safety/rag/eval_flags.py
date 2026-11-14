"""Gold retrieval through the real path: Flag -> retriever -> PostgreSQL.

    python -m drug_safety.rag.eval_flags

The 28 gold cases written as engine-style Flags (tests/gold/retrieval_flags.yaml). The retriever
builds its own query (flag.query_text()) and filter (flag.drug_ids()). Checks:
  - every flag's expected guideline or label passage is in its evidence (spec definition of
    done): the expected label passage (tests/gold/retrieval_labels.yaml, written before any
    model ran), or, for a gene flag, the CPIC recommendation for the same drug and phenotype.
    Both are counted separately;
  - CPIC recommendations reach gene flags only, and only for the flag's own phenotype;
  - no lab flag gets a supporting paper (none were collected for lab flags);
  - the evidence for the spec's worked example (p18) fits a 1,500-token context, and trimming
    keeps each flag's expected passage.
Prints the supporting papers so a person can read them.
"""

from __future__ import annotations

import json
import sys

import yaml

from drug_safety.config import get_settings
from drug_safety.contracts import Flag
from drug_safety.db import connect
from drug_safety.rag.chunk_bdpm import read_bdpm_chunks
from drug_safety.rag.chunk_cpic import read_cpic_chunks
from drug_safety.rag.chunk_dpwg import read_dpwg_chunks
from drug_safety.rag.chunk_papers import read_paper_chunks
from drug_safety.rag.chunking import bge_token_counter, read_chunks
from drug_safety.rag.embeddings import Embedder
from drug_safety.rag.eval_retrieval import GOLD_FILE, matches
from drug_safety.rag.retriever import PgRepository, build_context, retrieve

FLAGS_FILE = "tests/gold/retrieval_flags.yaml"
WORKED_EXAMPLE = ["gene-clopidogrel-cyp2c19-pm", "ddi-clopidogrel-omeprazole", "lab-metformin-egfr"]


GUIDELINES = {"cpic", "dpwg"}  # one recommendation per drug x phenotype


def cpic_matches(chunk, flag: Flag) -> bool:
    """Checked from the chunk's own text, independently of the retriever's rule."""
    if (
        chunk.source not in GUIDELINES
        or flag.flag_type != "gene_drug"
        or chunk.drugs != [flag.drug]
    ):
        return False
    want = (flag.phenotype or "").lower()
    for line in chunk.text.splitlines():
        if line.startswith(f"{flag.gene} phenotype: "):
            got = line.split(": ", 1)[1].lower().removeprefix("likely ")
            return got == want
    return False


def evaluate(s, embedder) -> tuple[list[dict], list[str], dict]:
    """Run the 28 gold flags through retrieve() and PostgreSQL.
    Returns (one row per flag, failures, the retrieved evidence by case)."""
    expect = {c["id"]: c for c in yaml.safe_load(open(GOLD_FILE))["cases"]}
    flags = yaml.safe_load(open(FLAGS_FILE))["flags"]
    chunks = {
        c.chunk_id: c
        for c in read_chunks(s)
        + read_paper_chunks(s)
        + read_cpic_chunks(s)
        + read_dpwg_chunks(s)
        + read_bdpm_chunks(s)
    }
    manifest = json.loads((s.data_dir / "processed/chunks/dailymed_manifest.json").read_text())
    label_of = {r["doc_id"]: r["drug"] for r in manifest}
    failures, rows, evidence = [], [], {}
    if {f["case"] for f in flags} != set(expect):
        failures.append("flag file and gold file list different cases")

    def has_expected(case, flag, ev) -> tuple[bool, bool]:
        label_ok = any(
            matches(chunks[r.evidence.chunk_id], label_of.get(r.evidence.doc_id), x)
            for r in ev
            for x in expect[case]["expect"]
        )
        return label_ok, any(cpic_matches(chunks[r.evidence.chunk_id], flag) for r in ev)

    with connect() as conn:
        repo = PgRepository(conn)
        for f in flags:
            flag = Flag.model_validate(f["flag"])
            ev = retrieve(flag, repo, embedder)
            evidence[f["case"]] = ev
            label_ok, guide_ok = has_expected(f["case"], flag, ev)
            cpic = [chunks[r.evidence.chunk_id] for r in ev if r.evidence.source in GUIDELINES]
            if cpic and not (flag.flag_type == "gene_drug" and all(cpic_matches(c, flag)
                                                                   for c in cpic)):  # fmt: skip
                failures.append(f"{f['case']}: a CPIC recommendation for another phenotype or "
                                "flag type")  # fmt: skip
            if not (label_ok or guide_ok):
                failures.append(f"{f['case']}: expected guideline or label passage not in evidence")
            papers = [r for r in ev if r.evidence.source == "pmc"]
            if flag.flag_type == "lab_drug" and papers:
                failures.append(f"{f['case']}: a lab flag got a supporting paper")
            rows.append({
                "case": f["case"], "flag_type": str(flag.flag_type), "hit": label_ok or guide_ok,
                "label": label_ok, "cpic": guide_ok,
                "chunks": [(r.evidence.chunk_id, r.evidence.source, round(r.distance, 3))
                           for r in ev],
                "paper": (papers[0].evidence.title, round(papers[0].distance, 3)) if papers
                else None,
            })  # fmt: skip

    count = bge_token_counter(s.embedding_model)
    worked = {c: evidence[c] for c in WORKED_EXAMPLE}
    ctx = build_context(worked, 1500, count)
    used = sum(count(r.evidence.text) for rs in ctx.values() for r in rs)
    if not all(ctx.values()) or used > 1500:
        failures.append("worked example evidence doesn't fit a 1,500-token context")
    for case, kept in ctx.items():  # trimming must never drop the evidence that matters
        flag = next(Flag.model_validate(f["flag"]) for f in flags if f["case"] == case)
        if not any(has_expected(case, flag, kept)):
            failures.append(f"{case}: context trimming dropped the expected passage")
    evidence["_worked_example"] = (sum(len(v) for v in worked.values()),
                                   sum(len(v) for v in ctx.values()), used)  # fmt: skip
    return rows, failures, evidence


def main() -> int:
    s = get_settings()
    rows, failures, evidence = evaluate(s, Embedder(s.embedding_model, s.cache_dir))
    for r in rows:
        src = "+".join(x for x in ("label", "cpic") if r[x]).replace("cpic", "CPIC") or "-"
        paper = f"paper d={r['paper'][1]:.3f}: {(r['paper'][0] or '')[:70]}" if r["paper"] else (
            "labels only")  # fmt: skip
        print(f"{'ok  ' if r['hit'] else 'MISS'} {r['case']:30} {src:11} {paper}")
    n_ret, n_kept, used = evidence["_worked_example"]
    print(f"\nworked example (p18): {n_ret} chunks retrieved, {n_kept} kept in a 1,500-token "
          f"context ({used} tokens)")  # fmt: skip
    gene = [r for r in rows if r["flag_type"] == "gene_drug"]
    print(f"gold through the contract: {sum(r['hit'] for r in rows)}/{len(rows)} (expected label "
          f"passage: {sum(r['label'] for r in rows)}/{len(rows)}; CPIC recommendation: "
          f"{sum(r['cpic'] for r in gene)}/{len(gene)} gene flags)")  # fmt: skip
    for x in failures:
        print("FAIL ", x)
    print("RESULT:", "PASS" if not failures else "FAIL")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
