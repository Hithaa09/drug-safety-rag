"""Database tests for rag/store.py, on a throwaway database (drugsafety_test).

Skipped automatically when the local database isn't running (`make db`).
Uses a fake embedder, so no model download is needed.
"""

import hashlib
import json

import numpy as np
import psycopg
import pytest

from drug_safety import config, db
from drug_safety.config import Settings
from drug_safety.ingest.dailymed import LabelDocument, LabelSection
from drug_safety.rag import chunking as ch
from drug_safety.rag import store
from drug_safety.rag.tagging import default_tagger

ADMIN_URL = "postgresql://drugsafety:drugsafety@localhost:5432/drugsafety"
TEST_URL = "postgresql://drugsafety:drugsafety@localhost:5432/drugsafety_test"


def _db_up() -> bool:
    try:
        psycopg.connect(ADMIN_URL, connect_timeout=2).close()
        return True
    except psycopg.OperationalError:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="local database not running (make db)")


class FakeEmbedder:
    model_name = "fake-768"

    @staticmethod
    def _vec(text):
        seed = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
        v = np.random.default_rng(seed).standard_normal(768).astype(np.float32)
        return v / np.linalg.norm(v)

    def embed_documents(self, texts):
        return np.stack([self._vec(t) for t in texts])

    def embed_query(self, text):
        return self._vec(text)


@pytest.fixture
def test_db(monkeypatch):
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS drugsafety_test")
        conn.execute("CREATE DATABASE drugsafety_test")
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    config.get_settings.cache_clear()
    db.migrate(TEST_URL)
    yield TEST_URL
    config.get_settings.cache_clear()
    with psycopg.connect(ADMIN_URL, autocommit=True) as conn:
        conn.execute("DROP DATABASE IF EXISTS drugsafety_test WITH (FORCE)")


def _label(setid, drug, text):
    return LabelDocument(
        doc_id=f"dailymed:{setid}",
        external_id=setid,
        title=f"{drug.upper()} TABLET [X]",
        labeler="X",
        doc_type_code="34391-3",
        doc_type="HUMAN PRESCRIPTION DRUG LABEL",
        source_version="1",
        effective_date="2025-01-01",
        pub_year=2025,
        drugs=[drug],
        raw_path="x",
        retrieved_at="2026-01-01T00:00:00+00:00",
        load_id="dailymed-x",
        sections=[
            LabelSection(
                section_key="drug_interactions",
                section_code="34073-7",
                code="42229-5",
                heading_path=["7 DRUG INTERACTIONS"],
                text=text,
            )
        ],
    )


def _prepare(tmp_path, labels):
    out = tmp_path / "processed" / "dailymed"
    out.mkdir(parents=True, exist_ok=True)
    for d in labels:
        (out / f"{d.external_id}.json").write_text(d.model_dump_json())
    (out / "_manifest.json").write_text(
        json.dumps([{"drug": d.drugs[0], "status": "ok", "setid": d.external_id} for d in labels])
    )
    (tmp_path / "processed" / "drug_classes.json").write_text(
        json.dumps({"attribution": "", "drugs": []})
    )
    settings = Settings(_env_file=None, data_dir=tmp_path, database_url=TEST_URL)
    ch.run(settings, lambda t: len(t.split()), default_tagger())
    return settings


def _counts(url):
    with psycopg.connect(url) as conn:
        return (
            conn.execute("SELECT count(*) FROM kb.documents").fetchone()[0],
            conn.execute("SELECT count(*) FROM kb.evidence_chunks").fetchone()[0],
        )


A = "11111111-0000-0000-0000-000000000001"
B = "11111111-0000-0000-0000-000000000002"


def test_load_then_reload_replaces_and_removes_unpinned(test_db, tmp_path):
    s = _prepare(
        tmp_path,
        [
            _label(A, "clopidogrel", "Avoid omeprazole."),
            _label(B, "warfarin", "NSAIDs increase bleeding."),
        ],
    )
    store.load(s, FakeEmbedder())
    assert _counts(test_db) == (2, 2)
    store.load(s, FakeEmbedder())  # rerun: same rows, nothing duplicated
    assert _counts(test_db) == (2, 2)

    s = _prepare(tmp_path / "v2", [_label(A, "clopidogrel", "Avoid omeprazole.")])
    result = store.load(s, FakeEmbedder())  # warfarin label no longer pinned
    assert result["documents_removed"] == 1
    assert _counts(test_db) == (1, 1)


def test_failed_load_changes_nothing(test_db, tmp_path):
    s = _prepare(tmp_path, [_label(A, "clopidogrel", "Avoid omeprazole.")])
    store.load(s, FakeEmbedder())
    before = _counts(test_db)

    # Corrupt the chunk file: a chunk pointing at a document that doesn't exist.
    path = tmp_path / "processed" / "chunks" / "dailymed.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    rows[0]["doc_id"] = "dailymed:does-not-exist"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        store.load(s, FakeEmbedder())
    assert _counts(test_db) == before  # the transaction rolled back


def test_remove_by_load_id(test_db, tmp_path):
    s = _prepare(tmp_path, [_label(A, "clopidogrel", "Avoid omeprazole.")])
    load_id = store.load(s, FakeEmbedder())["load_id"]
    assert store.remove(load_id) == (1, 1)
    assert _counts(test_db) == (0, 0)


def test_sql_search_filters_by_drug(test_db, tmp_path):
    s = _prepare(
        tmp_path,
        [
            _label(A, "clopidogrel", "Avoid omeprazole."),
            _label(B, "warfarin", "NSAIDs increase bleeding."),
        ],
    )
    store.load(s, FakeEmbedder())
    with db.connect(test_db) as conn:
        q = FakeEmbedder().embed_query("anything")
        assert all(
            cid.startswith(f"dailymed:{A}") for cid in store.search_sql(conn, q, ["clopidogrel"])
        )
        assert store.search_sql(conn, q, ["metformin"]) == []


def test_pg_repository_search_and_get(test_db, tmp_path):
    from drug_safety.rag.retriever import PgRepository

    s = _prepare(tmp_path, [_label(A, "clopidogrel", "Avoid omeprazole.")])
    store.load(s, FakeEmbedder())
    with db.connect(test_db) as conn:
        repo = PgRepository(conn)
        q = FakeEmbedder().embed_query("x")
        hits = repo.search(q, ["clopidogrel"], True, 3)
        assert [h.chunk_id.split(":")[1] for h in hits] == [A]
        assert repo.search(q, ["clopidogrel"], False, 3) == []  # no papers loaded
        got = repo.get([hits[0].chunk_id])[hits[0].chunk_id]
        assert got.title == "CLOPIDOGREL TABLET [X]" and got.tier == 1 and "omeprazole" in got.text
        with pytest.raises(ValueError):
            repo.search(q, ["clopidogrel"], True, 3, column="text; DROP TABLE x")  # no injection


class MustNotEmbed(FakeEmbedder):
    def embed_documents(self, texts):
        raise AssertionError("an empty source must not reach the embedder")


def test_empty_source_loads_and_leaves_other_sources(test_db, tmp_path, monkeypatch):
    """BDPM has no chunks until a translation is reviewed: loading it must work and change
    nothing else (the real embedder crashed on an empty list, found 2026-11-14)."""
    s = _prepare(tmp_path, [_label(A, "clopidogrel", "Avoid omeprazole.")])
    store.load(s, FakeEmbedder())
    monkeypatch.setitem(store.SOURCES, "bdpm", (lambda _: [], lambda _: []))
    result = store.load(s, MustNotEmbed(), source="bdpm")
    assert result["documents"] == 0 and result["chunks"] == 0
    assert _counts(test_db) == (1, 1)
