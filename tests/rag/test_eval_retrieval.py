"""Retrieval evaluation logic (offline, with made-up vectors; no model needed)."""

import numpy as np
import yaml

from drug_safety.rag import eval_retrieval as ev
from drug_safety.rag.chunking import Chunk


def chunk(i, drugs, tier=1, heading="7 DRUG INTERACTIONS", text="x"):
    return Chunk(
        chunk_id=f"c{i}",
        doc_id=f"d{i}",
        source="dailymed",
        tier=tier,
        section="drug_interactions",
        headings=[[heading]],
        text=text,
        drugs=drugs,
        genes=[],
        token_count=1,
        source_version="1",
        license="",
        load_id="l",
    )


def unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_search_filters_by_drug_before_ranking():
    chunks = [chunk(0, ["simvastatin"]), chunk(1, ["clopidogrel"]), chunk(2, ["clopidogrel"])]
    vecs = np.stack([unit([1, 0]), unit([0.9, 0.1]), unit([0, 1])])
    top, _, pool = ev.search(unit([1, 0]), chunks, vecs, ["clopidogrel"])
    assert pool == 2
    assert top == [1, 2]  # the closest chunk overall (a statin) is never returned


def test_tier_balanced_selection_keeps_authority_and_support():
    # Many close papers (tier 3) and one farther label (tier 1): the label must still be in,
    # alongside the best paper. The spec's "closest 10, sort by tier" lost labels this way.
    chunks = [chunk(i, ["warfarin"], tier=3) for i in range(12)] + [chunk(12, ["warfarin"], tier=1)]
    vecs = np.stack([unit([1, 0.01 * i]) for i in range(12)] + [unit([0.2, 1])])
    top, _, _ = ev.search(unit([1, 0]), chunks, vecs, ["warfarin"])
    assert top == [12, 0, 1]  # label first, then the closest papers to fill k=3


def test_assemble_fills_from_the_other_side_when_one_is_short():
    assert ev.assemble(["L1", "L2", "L3"], [], k=3) == ["L1", "L2", "L3"]  # labels only
    assert ev.assemble([], ["P1", "P2", "P3"], k=3) == ["P1", "P2", "P3"]  # papers only
    assert ev.assemble(["L1", "L2", "L3"], ["P1", "P2"], k=3) == ["L1", "L2", "P1"]


def test_matches_heading_and_text():
    c = chunk(0, ["clopidogrel"], heading="7.2 CYP2C19 Inhibitors", text="Avoid omeprazole.")
    assert ev.matches(c, "clopidogrel", {"label": "clopidogrel", "heading": "cyp2c19 inhibitors"})
    assert ev.matches(c, "clopidogrel", {"label": "clopidogrel", "text": ["OMEPRAZOLE", "avoid"]})
    assert not ev.matches(c, "warfarin", {"label": "clopidogrel", "text": ["omeprazole"]})
    assert not ev.matches(c, "clopidogrel", {"label": "clopidogrel", "text": ["pantoprazole"]})


def test_random_baseline():
    assert ev.random_baseline(10, 0) == 0.0
    assert ev.random_baseline(3, 1) == 1.0
    assert round(ev.random_baseline(10, 1), 2) == 0.3


def test_gold_file_is_well_formed():
    cases = yaml.safe_load(open(ev.GOLD_FILE))["cases"]
    assert len({c["id"] for c in cases}) == len(cases)
    types = {c["type"] for c in cases}
    assert types == {"gene_drug", "drug_drug", "lab_drug", "lifestyle_drug"}  # all 4 flag types
    for c in cases:
        assert c["query"] and c["filter"] and c["expect"], c["id"]
        for e in c["expect"]:
            assert e["label"] and ("heading" in e or "text" in e), c["id"]


def test_far_paper_is_not_used_as_support():
    # A label and a paper that is too far from the query: return labels only.
    chunks = [
        chunk(0, ["metformin"], tier=1),
        chunk(1, ["metformin"], tier=1),
        chunk(2, ["metformin"], tier=3),
        chunk(3, ["metformin"], tier=1),
    ]
    vecs = np.stack([unit([1, 0]), unit([1, 0.1]), unit([0.2, 1]), unit([1, 0.2])])
    top, _, _ = ev.search(unit([1, 0]), chunks, vecs, ["metformin"])
    assert top == [0, 1, 3]  # the far paper (index 2) is left out
