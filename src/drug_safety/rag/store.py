"""Load documents and embedded chunks into PostgreSQL, and check them.

Usage:
    python -m drug_safety.rag.store load --source SOURCE  # dailymed|pmc|cpic|dpwg|bdpm
    python -m drug_safety.rag.store verify                        # checks, incl. gold via SQL
    python -m drug_safety.rag.store remove --load-id ID           # delete one load (spec)

`load` replaces one source's rows in ONE transaction: its documents are upserted, their chunks
replaced, and documents of that source no longer present are removed. Other sources are never
touched. If anything fails, nothing changes.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime

import numpy as np
import yaml
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.db import connect
from drug_safety.ingest.cpic import load_cpic_docs
from drug_safety.ingest.dpwg import load_dpwg_docs
from drug_safety.rag.chunk_bdpm import load_bdpm_docs, read_bdpm_chunks
from drug_safety.rag.chunk_cpic import read_cpic_chunks
from drug_safety.rag.chunk_dpwg import read_dpwg_chunks
from drug_safety.rag.chunk_papers import load_paper_docs, read_paper_chunks
from drug_safety.rag.chunking import load_label_docs, read_chunks
from drug_safety.rag.embeddings import Embedder
from drug_safety.rag.eval_retrieval import GOLD_FILE, matches, search
from drug_safety.rag.retriever import PgRepository, select

log = logging.getLogger(__name__)

SOURCES = {
    "dailymed": (load_label_docs, read_chunks),
    "pmc": (load_paper_docs, read_paper_chunks),
    "cpic": (load_cpic_docs, read_cpic_chunks),
    "dpwg": (load_dpwg_docs, read_dpwg_chunks),
    "bdpm": (load_bdpm_docs, read_bdpm_chunks),  # only translation-reviewed passages
}
K = 3


def _as_array(v) -> np.ndarray:
    """pgvector may return its own Vector type or a numpy array, depending on version."""
    return np.asarray(v.to_numpy() if hasattr(v, "to_numpy") else v, dtype=np.float32)


def search_sql(conn, qvec: np.ndarray, drugs: list[str], k: int = K) -> list[str]:
    """The retriever's rule over PostgreSQL (rag/retriever.py, PgRepository)."""
    return [h.chunk_id for h in select(PgRepository(conn), qvec, drugs, k)]


def _available(settings: Settings, source: str) -> bool:
    try:
        SOURCES[source][1](settings)
        return True
    except FileNotFoundError:
        return False


def load(settings: Settings, embedder: Embedder, source: str = "dailymed") -> dict:
    load_docs, load_chunks = SOURCES[source]
    docs = load_docs(settings)
    chunks = load_chunks(settings)
    # A source can be empty (BDPM before any translation is reviewed): nothing to embed.
    vectors = embedder.embed_documents([c.text for c in chunks]) if chunks else []
    load_id = f"store-{source}-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    doc_ids = [d.doc_id for d in docs]

    with connect() as conn, conn.transaction():
        for d in docs:
            conn.execute(
                """INSERT INTO kb.documents (doc_id, source, external_id, title, labeler, drugs,
                       genes, study_type, tier, pub_year, license, raw_path, source_version,
                       retrieved_at, load_id, pmid, doi, journal)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (doc_id) DO UPDATE SET title=EXCLUDED.title,
                       pmid=EXCLUDED.pmid, doi=EXCLUDED.doi, journal=EXCLUDED.journal,
                       labeler=EXCLUDED.labeler, drugs=EXCLUDED.drugs, genes=EXCLUDED.genes,
                       study_type=EXCLUDED.study_type, tier=EXCLUDED.tier,
                       pub_year=EXCLUDED.pub_year, license=EXCLUDED.license,
                       raw_path=EXCLUDED.raw_path, source_version=EXCLUDED.source_version,
                       retrieved_at=EXCLUDED.retrieved_at, load_id=EXCLUDED.load_id""",
                [
                    d.doc_id,
                    d.source,
                    d.external_id,
                    d.title,
                    getattr(d, "labeler", None),
                    d.drugs,
                    sorted({g for c in chunks if c.doc_id == d.doc_id for g in c.genes}),
                    d.study_type,
                    d.tier,
                    d.pub_year,
                    d.license,
                    d.raw_path,
                    getattr(d, "source_version", None),
                    d.retrieved_at,
                    load_id,
                    getattr(d, "pmid", None),
                    getattr(d, "doi", None),
                    getattr(d, "journal", None),
                ],
            )
        removed = conn.execute(
            "DELETE FROM kb.documents WHERE source = %s AND NOT (doc_id = ANY(%s))",
            [source, doc_ids],
        ).rowcount
        conn.execute("DELETE FROM kb.evidence_chunks WHERE doc_id = ANY(%s)", [doc_ids])
        with conn.cursor().copy(
            "COPY kb.evidence_chunks (chunk_id, doc_id, source, tier, section, headings, text,"
            " drugs, genes, drug_classes, class_drugs, token_count, source_version, license,"
            " chunker_version, embedding, embedding_model, load_id) FROM STDIN"
        ) as copy:
            for c, v in zip(chunks, vectors, strict=True):
                copy.write_row(
                    [
                        c.chunk_id,
                        c.doc_id,
                        c.source,
                        c.tier,
                        c.section,
                        Jsonb(c.headings),
                        c.text,
                        c.drugs,
                        c.genes,
                        c.drug_classes,
                        c.class_drugs,
                        c.token_count,
                        c.source_version,
                        c.license,
                        c.chunker_version,
                        v,
                        embedder.model_name,
                        load_id,
                    ]
                )
    return {
        "load_id": load_id,
        "documents": len(docs),
        "chunks": len(chunks),
        "documents_removed": removed,
    }


def remove(load_id: str) -> tuple[int, int]:
    with connect() as conn, conn.transaction():
        n_chunks = conn.execute(
            "DELETE FROM kb.evidence_chunks WHERE load_id = %s", [load_id]
        ).rowcount
        n_docs = conn.execute("DELETE FROM kb.documents WHERE load_id = %s", [load_id]).rowcount
    return n_docs, n_chunks


# ---------------------------------------------------------------- verify


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


def verify(settings: Settings, embedder: Embedder) -> list[Check]:
    checks: list[Check] = []

    def add(name, ok, detail=""):
        checks.append(Check(name=name, ok=ok, detail="" if ok else detail))

    sources = [s for s in SOURCES if _available(settings, s)]
    docs = [d for s in sources for d in SOURCES[s][0](settings)]
    chunks = [c for s in sources for c in SOURCES[s][1](settings)]
    by_id = {c.chunk_id: c for c in chunks}
    add("report: sources checked", True)
    checks[-1].detail = ", ".join(sources)
    with connect() as conn:
        db_docs = {
            r[0]
            for r in conn.execute(
                "SELECT doc_id FROM kb.documents WHERE source = ANY(%s)", [sources]
            )
        }
        add(
            "documents match the processed files",
            db_docs == {d.doc_id for d in docs},
            f"db {len(db_docs)} vs files {len(docs)}",
        )
        rows = conn.execute(
            "SELECT chunk_id, text, drugs, genes, drug_classes, class_drugs, embedding,"
            " embedding_model, vector_dims(embedding) FROM kb.evidence_chunks"
            " WHERE source = ANY(%s)",
            [sources],
        ).fetchall()
        add(
            "chunks match the chunk files",
            {r[0] for r in rows} == set(by_id),
            f"db {len(rows)} vs file {len(by_id)}",
        )
        if not rows:
            add("database has label chunks", False, "table is empty; run `make store-labels`")
            return checks
        diff = [
            r[0]
            for r in rows
            if r[0] in by_id
            and (
                r[1] != by_id[r[0]].text
                or list(r[2]) != by_id[r[0]].drugs
                or list(r[3]) != by_id[r[0]].genes
                or list(r[4]) != by_id[r[0]].drug_classes
                or list(r[5]) != by_id[r[0]].class_drugs
            )
        ]
        add(
            "stored text and tags equal the chunk file",
            not diff,
            f"{len(diff)} differ; run `make store-labels`",
        )
        add(
            "every embedding has 768 dimensions",
            all(r[8] == 768 for r in rows),
            "wrong vector size",
        )
        add(
            f"embedding model is {embedder.model_name}",
            {r[7] for r in rows} == {embedder.model_name},
            str({r[7] for r in rows}),
        )
        # Stored vectors must equal the cached embedding of the same text.
        cached = embedder.embed_documents([by_id[r[0]].text for r in rows if r[0] in by_id])
        stored = np.stack([_as_array(r[6]) for r in rows if r[0] in by_id])
        add(
            "stored vectors equal the embedding of their text",
            bool(np.allclose(stored, cached, atol=1e-5)),
            "vectors differ; reload",
        )
        orphans = conn.execute(
            "SELECT count(*) FROM kb.documents d WHERE source = ANY(%s) AND NOT EXISTS "
            "(SELECT 1 FROM kb.evidence_chunks c WHERE c.doc_id = d.doc_id)",
            [sources],
        ).fetchone()[0]
        add("every document has chunks", orphans == 0, f"{orphans} without chunks")

        # Gold retrieval through SQL must equal the in-memory evaluation, and pass.
        cases = yaml.safe_load(open(GOLD_FILE, encoding="utf-8"))["cases"]
        # Expectations name a label's drug; paper chunks never match a label expectation.
        label_of = {d.doc_id: (d.drugs[0] if d.source == "dailymed" else None) for d in docs}
        vectors = embedder.embed_documents([c.text for c in chunks])
        hits, same = 0, 0
        for case in cases:
            q = embedder.embed_query(case["query"])
            sql_top = search_sql(conn, q, case["filter"])
            mem_top = [chunks[i].chunk_id for i in search(q, chunks, vectors, case["filter"])[0]]
            same += sql_top == mem_top
            hits += any(
                matches(by_id[cid], label_of[by_id[cid].doc_id], e)
                for cid in sql_top
                for e in case["expect"]
            )
        add(
            f"gold retrieval via SQL (all sources): {hits}/{len(cases)} in top 3",
            hits == len(cases),
            f"{len(cases) - hits} misses; run `make eval-retrieval` for details",
        )
        add(
            "SQL search gives the same top 3 as the in-memory evaluation",
            same == len(cases),
            f"{len(cases) - same} cases differ",
        )
    return checks


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    logging.basicConfig(level="WARNING", format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m drug_safety.rag.store")
    sub = p.add_subparsers(dest="cmd", required=True)
    ld = sub.add_parser("load")
    ld.add_argument("--source", choices=list(SOURCES), default="dailymed")
    sub.add_parser("verify")
    rm = sub.add_parser("remove")
    rm.add_argument("--load-id", required=True)
    args = p.parse_args(argv)

    if args.cmd == "remove":
        n_docs, n_chunks = remove(args.load_id)
        print(f"removed {n_docs} documents and {n_chunks} chunks with load_id {args.load_id}")
        return 0
    embedder = Embedder(settings.embedding_model, settings.cache_dir)
    if args.cmd == "load":
        print(json.dumps(load(settings, embedder, args.source), indent=2))
        return 0
    checks = verify(settings, embedder)
    failed = [c for c in checks if not c.ok]
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name}" + (f": {c.detail}" if c.detail else ""))
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed.")
    print("RESULT:", "PASS" if not failed else "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
