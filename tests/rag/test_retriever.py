"""Retriever behaviour (offline: in-memory repository, made-up vectors, fake embedder)."""

import numpy as np
import pytest

from drug_safety.contracts import Flag
from drug_safety.rag import retriever as rt
from drug_safety.rag.chunking import Chunk


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def chunk(cid, drugs, tier=1, class_drugs=(), text="x"):
    return Chunk(
        chunk_id=cid,
        doc_id=f"doc-{cid}",
        source="dailymed" if tier == 1 else "pmc",
        tier=tier,
        section="s",
        headings=[["H"]],
        text=text,
        drugs=list(drugs),
        genes=[],
        class_drugs=list(class_drugs),
        token_count=1,
        source_version="1",
        license="L",
        load_id="l",
    )


class FakeEmbedder:
    def embed_query(self, text):
        return unit([1, 0])


def repo(chunks, vecs):
    return rt.InMemoryRepository(chunks, np.stack([unit(v) for v in vecs]))


GENE = dict(
    flag_id="gene-001",
    flag_type="gene_drug",
    drug="clopidogrel",
    severity="major",
    rule_id="gene_drug:CYP2C19:pm:clopidogrel",
    reason="r",
    source="CPIC",
    gene="CYP2C19",
    phenotype="poor metabolizer",
)


def test_interaction_flag_filters_on_both_drugs():
    flag = Flag(
        flag_id="ddi-001",
        flag_type="drug_drug",
        drug="clopidogrel",
        severity="major",
        rule_id="drug_drug:clopidogrel:omeprazole",
        reason="r",
        source="x",
        interacting_drug="omeprazole",
    )
    r = repo(
        [chunk("a", ["omeprazole"]), chunk("b", ["warfarin"]), chunk("c", ["clopidogrel"])],
        [[1, 0], [1, 0], [0.9, 0.1]],
    )
    got = [x.evidence.chunk_id for x in rt.retrieve(flag, r, FakeEmbedder())]
    assert got == ["a", "c"]  # both drugs' chunks, never warfarin's


def test_gene_flag_gets_labels_and_a_close_paper():
    r = repo(
        [
            chunk("L1", ["clopidogrel"]),
            chunk("L2", ["clopidogrel"]),
            chunk("P1", ["clopidogrel"], tier=3),
        ],
        [[1, 0.3], [1, 0.4], [1, 0.1]],
    )
    got = rt.retrieve(Flag(**GENE), r, FakeEmbedder())
    assert [x.evidence.chunk_id for x in got] == ["L1", "L2", "P1"]
    assert got[2].distance <= rt.SUPPORT_MAX_DISTANCE and not got[2].via_class


def test_far_paper_dropped():
    r = repo(
        [chunk("L1", ["clopidogrel"]), chunk("P1", ["clopidogrel"], tier=3)], [[1, 0], [0.3, 1]]
    )
    assert [x.evidence.chunk_id for x in rt.retrieve(Flag(**GENE), r, FakeEmbedder())] == ["L1"]


def test_lab_flag_never_gets_a_paper():
    flag = Flag(
        flag_id="lab-001",
        flag_type="lab_drug",
        drug="metformin",
        severity="contraindicated",
        rule_id="lab_drug:egfr:lt30:metformin",
        reason="r",
        source="label",
        lab={"measure": "egfr", "value": 28, "unit": "u", "operator": "<", "threshold": 30},
    )
    r = repo([chunk("L1", ["metformin"]), chunk("P1", ["metformin"], tier=3)], [[1, 0.2], [1, 0]])
    assert [x.evidence.chunk_id for x in rt.retrieve(flag, r, FakeEmbedder())] == ["L1"]


def test_class_fallback_when_no_chunk_names_the_drug():
    flag = Flag(**GENE | {"drug": "ibuprofen", "rule_id": "gene_drug:CYP2C9:pm:ibuprofen"})
    r = repo([chunk("NSAID", ["clopidogrel"], class_drugs=["ibuprofen"])], [[1, 0]])
    [x] = rt.retrieve(flag, r, FakeEmbedder())
    assert x.evidence.chunk_id == "NSAID" and x.via_class


def test_no_evidence_returns_empty():
    assert (
        rt.retrieve(Flag(**GENE), repo([chunk("a", ["warfarin"])], [[1, 0]]), FakeEmbedder()) == []
    )


def rc(cid, tier, dist, text):
    return rt.RetrievedChunk(
        evidence=rt.EvidenceChunk(
            chunk_id=cid, doc_id="d", source="s", tier=tier, text=text, license="L"
        ),
        distance=dist,
    )


def test_build_context_drops_lowest_tier_then_farthest_and_keeps_every_flag():
    words = lambda t: len(t.split())  # noqa: E731
    ev = {
        "f1": [rc("L1", 1, 0.1, "a " * 10), rc("P1", 3, 0.1, "b " * 10)],
        "f2": [rc("L2", 1, 0.1, "c " * 10), rc("L3", 1, 0.3, "d " * 10)],
    }
    out = rt.build_context(ev, 25, words)
    assert [c.evidence.chunk_id for c in out["f1"]] == ["L1"]  # paper dropped first
    assert [c.evidence.chunk_id for c in out["f2"]] == ["L2"]  # then the farther label
    tiny = rt.build_context(ev, 5, words)  # can't fit: one chunk per flag
    assert all(len(v) == 1 for v in tiny.values())


def test_langchain_wrapper_returns_documents_with_ids():
    pytest.importorskip("langchain_core")
    r = repo([chunk("L1", ["clopidogrel"], text="label text")], [[1, 0]])
    docs = rt.langchain_retriever(r, FakeEmbedder(), ["clopidogrel"]).invoke("any query")
    assert docs[0].page_content == "label text" and docs[0].metadata["chunk_id"] == "L1"


def test_own_label_before_another_drugs_label_in_the_main_slots():
    """A warfarin flag: the Celebrex label mentions warfarin and is closer, but the warfarin
    label comes first. A fill-in slot (no paper) still goes by distance."""
    chunks = [
        chunk("celebrex-pm", ["warfarin", "celecoxib"], text="celecoxib | CELEBREX | 8 USE"),
        chunk("warfarin-pgx", ["warfarin"], text="warfarin | JANTOVEN | 12 CLINICAL"),
        chunk("warfarin-other", ["warfarin"], text="warfarin | JANTOVEN | 7 DRUG"),
    ]
    vecs = [[1, 0.05], [1, 0.3], [1, 0.6]]  # Celebrex closest, then the two warfarin chunks
    hits = rt.select(repo(chunks, vecs), unit([1, 0]), ["warfarin"], allow_support=False)
    assert [h.chunk_id for h in hits] == ["warfarin-pgx", "warfarin-other", "celebrex-pm"]
    # methotrexate + pantoprazole: two own-label passages fill the main slots; the fill-in slot
    # takes the closer omeprazole-label passage over a far pantoprazole one
    chunks = [
        chunk("own-a", ["pantoprazole"], text="pantoprazole | PROTONIX | 5 WARNINGS"),
        chunk("own-b", ["pantoprazole"], text="pantoprazole | PROTONIX | 7 DRUG"),
        chunk("own-far", ["pantoprazole"], text="pantoprazole | PROTONIX | 7 antiretrovirals"),
        chunk("omeprazole-mtx", ["methotrexate"], text="omeprazole | OMEPRAZOLE | 7 DRUG"),
    ]
    hits = rt.select(repo(chunks, [[1, 0.3], [1, 0.4], [1, 0.9], [1, 0.05]]), unit([1, 0]),
                     ["methotrexate", "pantoprazole"], allow_support=False)  # fmt: skip
    assert [h.chunk_id for h in hits] == ["own-a", "own-b", "omeprazole-mtx"]
