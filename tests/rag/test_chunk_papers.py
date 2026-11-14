"""Paper chunker (offline; tokens counted as words)."""

from drug_safety.ingest.pmc import PaperDocument, PaperSection
from drug_safety.rag import chunk_papers as cp
from drug_safety.rag.tagging import default_tagger


def words(t):
    return len(t.split())


def doc(sections, title="CYP2C19 genotype and clopidogrel outcomes"):
    return PaperDocument(
        doc_id="pmc:PMC1",
        external_id="PMC1",
        pmid="1",
        doi=None,
        title=title,
        journal="J",
        pub_year=2024,
        drugs=["clopidogrel"],
        genes=["CYP2C19"],
        study_type="clinical_study",
        tier=3,
        license="CC BY",
        full_text=True,
        license_note="",
        raw_path="x",
        retrieved_at="2026-11-11T00:00:00+00:00",
        load_id="pmc-x",
        sections=sections,
    )


def sec(section, text, heading=None, kind="paragraph"):
    return PaperSection(section=section, kind=kind, heading=heading or section.title(), text=text)


def test_context_line_tags_and_ids():
    t = default_tagger()
    [c] = (
        cp.chunk_papers
        if False
        else cp.chunk_paper(
            doc([sec("RESULTS", "Poor metabolizers had more events.", "Outcomes")]), words, t, "l"
        )
    )
    assert c.chunk_id == "pmc:PMC1:results:001"
    assert c.text.startswith(
        "paper | clopidogrel · CYP2C19 | CYP2C19 genotype and clopidogrel outcomes (2024)"
        " | Results\n\nOutcomes\n"
    )
    assert c.drugs == ["clopidogrel"] and c.genes == [
        "CYP2C19"
    ]  # from the title in the context line
    assert c.tier == 3 and c.license == "CC BY"


def test_sections_never_mixed_and_long_text_split():
    t = default_tagger()
    long = " ".join(f"Sentence {i} about warfarin dosing." for i in range(200))  # ~1000 words
    out = cp.chunk_paper(
        doc([sec("ABSTRACT", "Short abstract."), sec("DISCUSS", long, "Discussion")]), words, t, "l"
    )
    assert out[0].section == "abstract" and all(c.section == "discuss" for c in out[1:])
    assert len(out) >= 3 and all(c.token_count <= cp.MAX_TOKENS for c in out)


def test_paper_drugs_in_context_line_make_every_chunk_reachable():
    t = default_tagger()
    [c] = cp.chunk_paper(
        doc([sec("RESULTS", "Events were rare.")], title="Outcomes in a registry"), words, t, "l"
    )
    # The title names no drug, but selection found clopidogrel in the abstract: the context
    # line carries it, so the chunk is reachable by the drug filter.
    assert c.drugs == ["clopidogrel"] and "clopidogrel" in c.text.split("\n")[0]


def test_verify_detects_a_missing_chunk(monkeypatch):
    """The coverage check went through several fixes; prove it still catches real loss."""
    import pytest

    from drug_safety.config import get_settings
    from drug_safety.rag.chunking import bge_token_counter

    s = get_settings()
    try:
        real = cp.read_paper_chunks(s)
    except FileNotFoundError:
        pytest.skip("run make chunk-papers first")
    victim = next(c for c in real if c.section == "results" and c.chunk_id.endswith(":003"))
    monkeypatch.setattr(cp, "read_paper_chunks", lambda _s: [c for c in real if c is not victim])
    failed = {
        c.name
        for c in cp.verify(s, bge_token_counter(s.embedding_model), default_tagger())
        if not c.ok
    }
    assert "no text lost from kept sections (8-word windows)" in failed
