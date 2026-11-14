"""Retrieval evaluation on the gold label cases (tests/gold/retrieval_labels.yaml).

Usage:
    python -m drug_safety.rag.eval_retrieval                     # default model, with and
                                                                 # without the BGE instruction
    python -m drug_safety.rag.eval_retrieval --model NAME [--no-instruction]

Search mirrors the spec's retriever (RAG layer, steps 2-4), in memory so no database is needed:
  1. filter: keep chunks whose `drugs` overlap the flag's drugs;
  2. rank by cosine distance and take the closest 10;
  3. re-sort by evidence tier, then distance, and keep 3.
A case passes ("hit") if any of the 3 matches an expectation. The same gold file will later be
run against the real PostgreSQL retriever, which must give the same result.

Writes data/outputs/<date>_retrieval_eval/<model>[_noinstr].json with every case's ranking.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date
from math import comb
from pathlib import Path

import numpy as np
import yaml

from drug_safety.config import get_settings
from drug_safety.rag.chunking import Chunk, read_chunks
from drug_safety.rag.embeddings import BGE_QUERY_INSTRUCTION, Embedder

log = logging.getLogger(__name__)

GOLD_FILE = Path("tests/gold/retrieval_labels.yaml")
# The selection rule lives in rag/retriever.py (one copy, used by the retriever, this
# evaluation and the database check).
from drug_safety.rag.retriever import (  # noqa: E402
    AUTHORITY_K,
    SUPPORT_K,
    SUPPORT_MAX_DISTANCE,
    InMemoryRepository,
    K,
    assemble,
    select,
)

PASS_RATE = 1.0  # spec definition of done: every gold flag's expected chunk in the top 3
__all__ = ["AUTHORITY_K", "SUPPORT_K", "SUPPORT_MAX_DISTANCE", "assemble", "search", "matches"]


def matches(chunk: Chunk, label_drug: str, expectation: dict) -> bool:
    if label_drug != expectation["label"]:
        return False
    if "heading" in expectation and not any(
        expectation["heading"].lower() in " > ".join(h).lower() for h in chunk.headings
    ):
        return False
    return all(x.lower() in chunk.text.lower() for x in expectation.get("text", []))


def search(query_vec, chunks, vectors, drugs, k=K):
    """The retriever's rule (drug filter, tier-balanced selection) over in-memory chunks.
    Returns (selected indices, the whole filtered ranking for diagnostics, pool size)."""
    index = {c.chunk_id: i for i, c in enumerate(chunks)}
    top = [
        index[h.chunk_id] for h in select(InMemoryRepository(chunks, vectors), query_vec, drugs, k)
    ]
    pool = [i for i, c in enumerate(chunks) if set(c.drugs) & set(drugs)]
    dist = 1.0 - vectors[pool] @ query_vec
    full = [pool[j] for j in np.argsort(dist, kind="stable")]
    return top, full, len(pool)


def random_baseline(pool_size: int, n_matching: int, k: int = K) -> float:
    """Chance that k random chunks from the filtered pool include a matching one."""
    if n_matching == 0:
        return 0.0
    return 1 - comb(pool_size - n_matching, k) / comb(pool_size, k)


def evaluate(embedder: Embedder, chunks: list[Chunk], label_of: dict[str, str]) -> dict:
    cases = yaml.safe_load(open(GOLD_FILE, encoding="utf-8"))["cases"]
    vectors = embedder.embed_documents([c.text for c in chunks])
    rows = []
    for case in cases:
        top, full, pool = search(
            embedder.embed_query(case["query"]), chunks, vectors, case["filter"]
        )

        def ok(i, case=case):
            return any(matches(chunks[i], label_of[chunks[i].doc_id], e) for e in case["expect"])

        good = [r for r, i in enumerate(full, 1) if ok(i)]
        rows.append(
            {
                "id": case["id"],
                "type": case["type"],
                "hit": any(ok(i) for i in top),
                "first_match_rank": good[0] if good else None,
                "pool": pool,
                "matching_in_pool": len(good),
                "random_hit_chance": round(random_baseline(pool, len(good)), 3),
                "top3": [
                    {
                        "chunk_id": chunks[i].chunk_id,
                        "match": ok(i),
                        "heading": " > ".join(chunks[i].headings[0])[:90],
                    }
                    for i in top
                ],
            }
        )
    hits = sum(r["hit"] for r in rows)
    return {
        "model": embedder.model_name,
        "query_instruction": bool(embedder.query_instruction),
        "cases": len(rows),
        "hits": hits,
        "hit_rate": round(hits / len(rows), 3),
        "mrr": round(
            np.mean([1 / r["first_match_rank"] if r["first_match_rank"] else 0 for r in rows]), 3
        ),
        "random_baseline_hit_rate": round(np.mean([r["random_hit_chance"] for r in rows]), 3),
        "by_type": {
            t: f"{sum(r['hit'] for r in rows if r['type'] == t)}/"
            f"{sum(1 for r in rows if r['type'] == t)}"
            for t in dict.fromkeys(r["type"] for r in rows)
        },
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    logging.basicConfig(level="WARNING", format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m drug_safety.rag.eval_retrieval")
    p.add_argument("--model", default=settings.embedding_model)
    p.add_argument(
        "--no-instruction", action="store_true", help="only run without the BGE query instruction"
    )
    args = p.parse_args(argv)

    chunks = read_chunks(settings)
    try:  # search what the database holds: labels AND papers
        from drug_safety.rag.chunk_papers import read_paper_chunks

        chunks += read_paper_chunks(settings)
    except FileNotFoundError:
        pass
    manifest = json.loads(
        (settings.data_dir / "processed" / "chunks" / "dailymed_manifest.json").read_text()
    )
    # Expectations name a label's drug; paper chunks (absent here) never match one.
    label_of = {r["doc_id"]: r["drug"] for r in manifest}
    label_of |= {c.doc_id: None for c in chunks if c.source != "dailymed"}
    print(
        f"searching {len(chunks)} chunks ({sum(c.source == 'dailymed' for c in chunks)} label, "
        f"{sum(c.source != 'dailymed' for c in chunks)} paper)"
    )
    out_dir = settings.outputs_dir / f"{date.today():%Y-%m-%d}_retrieval_eval"
    out_dir.mkdir(parents=True, exist_ok=True)

    modes = [""] if args.no_instruction else ["", BGE_QUERY_INSTRUCTION]
    best = 0.0
    for instr in modes:
        result = evaluate(Embedder(args.model, settings.cache_dir, instr), chunks, label_of)
        name = re.sub(r"[^A-Za-z0-9]+", "_", args.model) + ("" if instr else "_noinstr")
        (out_dir / f"{name}.json").write_text(json.dumps(result, indent=2))
        print(f"\n== {args.model} | query instruction: {'yes' if instr else 'no'}")
        print(
            f"   hit@3 {result['hits']}/{result['cases']} ({result['hit_rate']:.0%})  "
            f"MRR {result['mrr']}  |  random order would give "
            f"{result['random_baseline_hit_rate']:.0%}"
        )
        print(f"   by flag type: {result['by_type']}")
        for r in result["rows"]:
            if not r["hit"]:
                print(
                    f"   MISS {r['id']:32} first match at rank {r['first_match_rank']} "
                    f"of {r['pool']}; got: " + " | ".join(t["heading"][:45] for t in r["top3"])
                )
        best = max(best, result["hit_rate"])
    print(f"\nResults saved in {out_dir}")
    print("RESULT:", "PASS" if best >= PASS_RATE else f"BELOW TARGET ({PASS_RATE:.0%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
