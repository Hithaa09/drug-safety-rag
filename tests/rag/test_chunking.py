"""Label chunker tests (offline). Tokens are counted as words so tests need no model download."""

import json

import pytest

from drug_safety.config import Settings
from drug_safety.ingest.dailymed import LabelDocument, LabelSection
from drug_safety.rag import chunking as ch
from drug_safety.rag.tagging import default_tagger


def words(text: str) -> int:
    return len(text.split())


def sec(key, path, text, code="42229-5", top="00000-0"):
    return LabelSection(section_key=key, section_code=top, code=code, heading_path=path, text=text)


def doc(sections, drug="clopidogrel", setid="11111111-2222-3333-4444-555555555555"):
    return LabelDocument(
        doc_id=f"dailymed:{setid}",
        external_id=setid,
        title="TESTDRUG TABLET [MAKER]",
        labeler="Maker",
        doc_type_code="34391-3",
        doc_type="HUMAN PRESCRIPTION DRUG LABEL",
        source_version="3",
        effective_date="2025-01-01",
        pub_year=2025,
        drugs=[drug],
        raw_path="x",
        retrieved_at="2026-01-01T00:00:00+00:00",
        load_id="dailymed-x",
        sections=sections,
    )


def sentences(n, word="word"):
    return " ".join(f"Sentence {i} has some {word} text." for i in range(n))


@pytest.fixture(scope="module")
def tagger():
    return default_tagger()


def test_small_subsections_packed_with_headings(tagger):
    d = doc(
        [
            sec("contraindications", ["4 CONTRAINDICATIONS", "4.1 Active Bleeding"], "Bleeding."),
            sec("contraindications", ["4 CONTRAINDICATIONS", "4.2 Hypersensitivity"], "Allergy."),
        ]
    )
    [c] = ch.chunk_label(d, words, tagger, "load")
    assert c.chunk_id == f"{d.doc_id}:contraindications:001"
    assert c.text == (
        "clopidogrel | TESTDRUG TABLET | 4 CONTRAINDICATIONS\n\n"
        "4.1 Active Bleeding\nBleeding.\n\n4.2 Hypersensitivity\nAllergy."
    )
    assert c.headings == [
        ["4 CONTRAINDICATIONS", "4.1 Active Bleeding"],
        ["4 CONTRAINDICATIONS", "4.2 Hypersensitivity"],
    ]


def test_top_level_sections_never_mixed_and_numbered_separately(tagger):
    d = doc(
        [
            sec("contraindications", ["4 CONTRAINDICATIONS"], "A."),
            sec("drug_interactions", ["7 DRUG INTERACTIONS"], "B."),
            sec("drug_interactions", ["7 DRUG INTERACTIONS", "7.1 X"], "C."),
        ]
    )
    out = ch.chunk_label(d, words, tagger, "load")
    assert [c.chunk_id.rsplit(":", 2)[1:] for c in out] == [
        ["contraindications", "001"],
        ["drug_interactions", "001"],
    ]


def test_packing_stops_near_target(tagger):
    subs = [
        sec("drug_interactions", ["7 DRUG INTERACTIONS", f"7.{i} X"], sentences(20))
        for i in range(1, 6)
    ]  # about 120 words each
    out = ch.chunk_label(doc(subs), words, tagger, "load")
    assert len(out) > 1
    assert all(c.token_count <= ch.TARGET_TOKENS for c in out)


def test_long_subsection_split_with_heading_repeated(tagger):
    long = "\n\n".join(sentences(15) for _ in range(8))  # about 720 words
    d = doc(
        [sec("clinical_pharmacology", ["12 CLINICAL PHARMACOLOGY", "12.5 Pharmacogenomics"], long)]
    )
    out = ch.chunk_label(d, words, tagger, "load")
    assert len(out) >= 2
    for c in out:
        assert c.token_count <= ch.MAX_TOKENS
        assert c.text.startswith(
            "clopidogrel | TESTDRUG TABLET | 12 CLINICAL PHARMACOLOGY\n\n12.5 Pharmacogenomics\n"
        )


def test_long_table_split_repeats_caption_and_header(tagger):
    rows = [f"row {i} | {sentences(1)} | value {i}" for i in range(80)]  # about 880 words
    table = "\n".join(["Table 3: Results", "Dose | Effect | Value", *rows, "Note 1: mean."])
    d = doc([sec("clinical_pharmacology", ["12 CLINICAL PHARMACOLOGY", "12.3 PK"], table)])
    out = ch.chunk_label(d, words, tagger, "load")
    assert len(out) >= 2
    for i, c in enumerate(out):
        lines = c.text.split("\n")
        assert "Dose | Effect | Value" in lines
        assert ("Table 3: Results" if i == 0 else "Table 3: Results (continued)") in lines
    assert "Note 1: mean." in out[-1].text
    body = "\n".join(c.text for c in out)
    assert all(r in body for r in rows)  # no row lost


def test_dropped_and_unknown_sections(tagger):
    d = doc(
        [
            sec("description", ["11 DESCRIPTION"], "Chemistry."),
            sec("drug_interactions", ["7 DRUG INTERACTIONS"], "Keep me."),
        ]
    )
    out = ch.chunk_label(d, words, tagger, "load")
    assert [c.section for c in out] == ["drug_interactions"]
    with pytest.raises(ch.UnknownSectionError):
        ch.chunk_label(doc([sec("brand_new_section", [], "x")]), words, tagger, "load")


def test_tags_include_own_drug_and_mentions(tagger):
    d = doc(
        [
            sec(
                "drug_interactions",
                ["7 DRUG INTERACTIONS"],
                "Avoid omeprazole. CYP2C19 poor metabolizers.",
            )
        ]
    )
    [c] = ch.chunk_label(d, words, tagger, "load")
    assert c.drugs == ["clopidogrel", "omeprazole"]
    assert c.genes == ["CYP2C19"]


def test_every_keep_and_drop_key_is_classified_once():
    assert not ch.KEEP_SECTIONS & set(ch.DROP_SECTIONS)


# ---------------------------------------------------------------- verify


def _write(tmp_path, docs):
    out = tmp_path / "processed" / "dailymed"
    out.mkdir(parents=True)
    for d in docs:
        (out / f"{d.external_id}.json").write_text(d.model_dump_json())
    manifest = [{"drug": d.drugs[0], "status": "ok", "setid": d.external_id} for d in docs]
    (out / "_manifest.json").write_text(json.dumps(manifest))
    classes = [
        {
            "drug": "ibuprofen",
            "epc": [],
            "moa": [],
            "classes": ["nsaid"],
            "source": "test",
            "retrieved_at": "x",
        }
    ]
    (tmp_path / "processed" / "drug_classes.json").write_text(
        json.dumps({"attribution": "", "drugs": classes})
    )
    return Settings(_env_file=None, data_dir=tmp_path)


def _sample():
    rows = [f"row {i} | {sentences(1)} | v{i}" for i in range(80)]
    return doc(
        [
            sec(
                "drug_interactions",
                ["7 DRUG INTERACTIONS", "7.2 CYP2C19 Inhibitors"],
                "Avoid omeprazole.",
            ),
            sec(
                "clinical_pharmacology",
                ["12 CLINICAL PHARMACOLOGY", "12.3 PK"],
                "\n".join(["Table 1: T", "Dose | Effect | Value", *rows]),
            ),
        ]
    )


def test_verify_passes_on_fresh_output(tmp_path, tagger):
    settings = _write(tmp_path, [_sample()])
    ch.run(settings, words, tagger)
    failed = [c for c in ch.verify(settings, words, tagger, gold=[], gold_classes=[]) if not c.ok]
    assert failed == []


def _rewrite_chunks(settings, change):
    path = settings.data_dir / "processed" / "chunks" / "dailymed.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    path.write_text("\n".join(json.dumps(r) for r in change(rows)) + "\n")


def test_verify_catches_lost_text(tmp_path, tagger):
    settings = _write(tmp_path, [_sample()])
    ch.run(settings, words, tagger)
    _rewrite_chunks(settings, lambda rows: rows[1:])
    failed = {
        c.name for c in ch.verify(settings, words, tagger, gold=[], gold_classes=[]) if not c.ok
    }
    assert "no text lost from kept sections" in failed


def test_verify_catches_table_rows_without_header(tmp_path, tagger):
    settings = _write(tmp_path, [_sample()])
    ch.run(settings, words, tagger)

    def drop_header(rows):
        for r in rows:
            r["text"] = r["text"].replace("Dose | Effect | Value\n", "")
            r["token_count"] = words(r["text"])
        return rows

    _rewrite_chunks(settings, drop_header)
    failed = {
        c.name for c in ch.verify(settings, words, tagger, gold=[], gold_classes=[]) if not c.ok
    }
    assert "table rows always come with their header row" in failed


def test_verify_catches_oversized_chunk(tmp_path, tagger):
    settings = _write(tmp_path, [_sample()])
    ch.run(settings, words, tagger)

    def bloat(rows):
        rows[0]["text"] += " extra" * 600
        rows[0]["token_count"] = words(rows[0]["text"])
        return rows

    _rewrite_chunks(settings, bloat)
    failed = {
        c.name for c in ch.verify(settings, words, tagger, gold=[], gold_classes=[]) if not c.ok
    }
    assert "fits the embedding model (<= 512 tokens)" in failed


def test_class_mentions_tagged_separately_from_drugs(tagger):
    d = doc(
        [
            sec(
                "drug_interactions",
                ["7 DRUG INTERACTIONS", "7.4 NSAIDs"],
                "Coadministration with NSAIDs increases bleeding risk.",
            )
        ]
    )
    [c] = ch.chunk_label(d, words, tagger, "load", {"nsaid": ["ibuprofen", "diclofenac"]})
    assert c.drugs == ["clopidogrel"]  # the drug filter stays exact
    assert c.drug_classes == ["nsaid"]
    assert c.class_drugs == ["diclofenac", "ibuprofen"]


def test_verify_catches_stale_class_tags(tmp_path, tagger):
    s = _sample()
    s.sections[0].text = "Avoid omeprazole and NSAIDs."
    settings = _write(tmp_path, [s])
    ch.run(settings, words, tagger)

    def drop_classes(rows):
        for r in rows:
            r["drug_classes"], r["class_drugs"] = [], []
        return rows

    _rewrite_chunks(settings, drop_classes)
    failed = {
        c.name for c in ch.verify(settings, words, tagger, gold=[], gold_classes=[]) if not c.ok
    }
    assert "tags match the current dictionaries (drugs, genes, classes)" in failed
