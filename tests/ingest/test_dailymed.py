"""Offline tests for the DailyMed loader (no network)."""

import json
import re
from pathlib import Path

import pytest

from drug_safety.config import Settings
from drug_safety.ingest import dailymed as dm
from drug_safety.scope import load_scope

FIXTURE = Path(__file__).parent / "fixtures" / "mini_spl.xml"
SETID = "11111111-2222-3333-4444-555555555555"


@pytest.fixture
def parsed():
    return dm.parse_spl(FIXTURE.read_bytes())


def test_metadata(parsed):
    assert parsed["setid"] == SETID
    assert parsed["version"] == "7"
    assert parsed["effective_date"] == "2025-05-30"
    assert parsed["doc_type_code"] == dm.RX_DOCTYPE
    assert parsed["labeler"] == "Test Pharma Inc."


def test_sections_and_keys(parsed):
    got = [(s.section_key, s.heading_path) for s in parsed["sections"]]
    assert got == [
        ("contraindications", ["4 CONTRAINDICATIONS"]),
        ("drug_interactions", ["7 DRUG INTERACTIONS"]),
        ("drug_interactions", ["7 DRUG INTERACTIONS", "7.2 CYP2C19 Inhibitors"]),
        ("clinical_pharmacology", ["12 CLINICAL PHARMACOLOGY", "12.5 Pharmacogenomics"]),
        ("unclassified", []),
    ]


def test_unclassified_subsection_found_by_title(parsed):
    pgx = [s for s in parsed["sections"] if "Pharmacogenomics" in " ".join(s.heading_path)]
    assert len(pgx) == 1
    assert pgx[0].section_code == "34090-1"
    assert "poor metabolizers" in pgx[0].text


def test_boilerplate_and_highlights_skipped(parsed):
    all_text = "\n".join(s.text for s in parsed["sections"])
    for marker in ("PRODUCT TABLE", "PACKAGE LABEL", "HIGHLIGHT"):
        assert marker not in all_text


def test_text_rendering(parsed):
    sub = parsed["sections"][2].text
    assert "Avoid omeprazole or esomeprazole.\nSecond line." in sub  # inline tags, <br/>
    assert "- First item\n" in sub and "- Second item" in sub  # list items
    assert (
        "Table 1: Examples\nDrug | Effect [note 1]\nomeprazole | reduces active metabolite\n"
        "Note 1: Measured at steady state"
    ) in sub  # table footnotes listed below the table, not inside header cells
    assert parsed["sections"][1].text == "Intro to interactions."  # whitespace collapsed
    assert "Avoid" not in parsed["sections"][1].text  # child text not duplicated in parent


@pytest.mark.parametrize(
    "title,query,expected",
    [
        ("PLAVIX (CLOPIDOGREL) TABLET, FILM COATED [SANOFI]", "clopidogrel", True),
        ("WARFARIN SODIUM TABLET [PD-RX]", "warfarin", True),
        ("EXCEDRIN (ACETAMINOPHEN, ASPIRIN, AND CAFFEINE) TABLET [X]", "aspirin", False),
        ("CALDOLOR (IBUPROFEN) INJECTION [CUMBERLAND]", "ibuprofen", False),
        ("AMLODIPINE AND BENAZEPRIL (AMLODIPINE AND BENAZEPRIL) CAPSULE [X]", "amlodipine", False),
        ("OMEPRAZOLE CAPSULE, DELAYED RELEASE [GLENMARK]", "omeprazole", True),  # comma in form
        ("ACETAMINOPHEN AND CODEINE PHOSPHATE TABLET [X]", "codeine", False),
        ("IBUPROFEN AND FAMOTIDINE TABLET, FILM COATED [X]", "ibuprofen", False),
    ],
)
def test_single_ingredient_oral(title, query, expected):
    assert dm.is_single_ingredient_oral(title, query) is expected


def test_pins_cover_every_scope_drug():
    pins = dm.load_pins()
    assert set(pins) == set(load_scope().drugs)
    uuid = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    for drug, pin in pins.items():
        assert pin.review in {"ok", "check", "missing"}, drug
        if pin.setid is None:
            assert pin.review == "missing" and pin.note, drug
        else:
            assert uuid.match(pin.setid), drug
    assert len({p.setid for p in pins.values() if p.setid}) == sum(
        1 for p in pins.values() if p.setid
    ), "two drugs share a set ID"


class FakeClient:
    def spl_xml(self, setid):
        return FIXTURE.read_bytes()


def test_run_load_writes_raw_processed_and_manifest(tmp_path, monkeypatch):
    pins = {
        "clopidogrel": dm.Pin(setid=SETID, title="TESTDRUG", kind="brand", review="ok"),
        "acenocoumarol": dm.Pin(setid=None, kind="none", review="missing", note="not in US"),
    }
    monkeypatch.setattr(dm, "load_pins", lambda: pins)
    settings = Settings(_env_file=None, data_dir=tmp_path)

    manifest = dm.run_load(["clopidogrel", "acenocoumarol"], FakeClient(), settings)

    raw = tmp_path / "raw" / "dailymed" / SETID / "v7.xml"
    assert raw.read_bytes() == FIXTURE.read_bytes()  # raw file kept untouched
    doc = json.loads((tmp_path / "processed" / "dailymed" / f"{SETID}.json").read_text())
    assert doc["doc_id"] == f"dailymed:{SETID}"
    assert doc["drugs"] == ["clopidogrel"] and doc["tier"] == 1
    assert doc["load_id"].startswith("dailymed-")
    assert len(doc["sections"]) == 5

    by_drug = {m["drug"]: m for m in manifest}
    assert by_drug["clopidogrel"]["status"] == "ok"
    assert by_drug["clopidogrel"]["has_drug_interactions"] is True
    assert by_drug["acenocoumarol"]["status"] == "missing"
    assert (tmp_path / "processed" / "dailymed" / "_manifest.json").exists()


def test_mismatched_setid_is_an_error(tmp_path, monkeypatch):
    pins = {
        "warfarin": dm.Pin(setid="99999999-0000-0000-0000-000000000000", kind="brand", review="ok")
    }
    monkeypatch.setattr(dm, "load_pins", lambda: pins)
    manifest = dm.run_load(["warfarin"], FakeClient(), Settings(_env_file=None, data_dir=tmp_path))
    assert manifest[0]["status"] == "error"


def test_untitled_subsections_merge_into_previous():
    xml = FIXTURE.read_bytes().replace(
        b"<title>7.2 CYP2C19 Inhibitors</title>",
        b"<title>7.2 CYP2C19 Inhibitors</title>"
        b"<component><section><code code='42229-5'/>"
        b"<text><paragraph>Untitled block</paragraph></text></section></component>",
    )
    sections = dm.parse_spl(xml)["sections"]
    sub = [s for s in sections if s.heading_path[-1:] == ["7.2 CYP2C19 Inhibitors"]]
    assert len(sub) == 1
    assert sub[0].text.endswith("Untitled block")


def test_partial_load_keeps_other_manifest_rows(tmp_path, monkeypatch):
    pins = {
        "clopidogrel": dm.Pin(setid=SETID, kind="brand", review="ok"),
        "acenocoumarol": dm.Pin(setid=None, kind="none", review="missing", note="not in US"),
    }
    monkeypatch.setattr(dm, "load_pins", lambda: pins)
    settings = Settings(_env_file=None, data_dir=tmp_path)
    dm.run_load(["clopidogrel", "acenocoumarol"], FakeClient(), settings)
    dm.run_load(["clopidogrel"], FakeClient(), settings)
    rows = json.loads((tmp_path / "processed" / "dailymed" / "_manifest.json").read_text())
    assert [r["drug"] for r in rows] == ["clopidogrel", "acenocoumarol"]


def test_untitled_coded_subsection_uses_code_name_as_heading():
    xml = FIXTURE.read_bytes().replace(
        b"<title>7.2 CYP2C19 Inhibitors</title>",
        b"<title>7.2 CYP2C19 Inhibitors</title>"
        b"<component><section><code code='50570-1' displayName='DO NOT USE SECTION'/>"
        b"<text><paragraph>if allergic</paragraph></text></section></component>",
    )
    sections = dm.parse_spl(xml)["sections"]
    hit = [s for s in sections if s.text == "if allergic"]
    assert hit[0].heading_path == ["7 DRUG INTERACTIONS", "7.2 CYP2C19 Inhibitors", "Do not use"]


def _loaded(tmp_path, monkeypatch):
    pins = {
        "clopidogrel": dm.Pin(setid=SETID, kind="brand", review="ok"),
        "acenocoumarol": dm.Pin(setid=None, kind="none", review="missing", note="not in US"),
    }
    monkeypatch.setattr(dm, "load_pins", lambda: pins)
    monkeypatch.setattr(
        dm, "load_scope", lambda: type("S", (), {"drugs": ["clopidogrel", "acenocoumarol"]})()
    )
    settings = Settings(_env_file=None, data_dir=tmp_path)
    dm.run_load(["clopidogrel", "acenocoumarol"], FakeClient(), settings)
    return settings


def test_verify_passes_on_good_output(tmp_path, monkeypatch):
    checks = dm.verify(_loaded(tmp_path, monkeypatch))
    assert [c for c in checks if not c.ok] == []


def test_verify_catches_deleted_raw_file(tmp_path, monkeypatch):
    settings = _loaded(tmp_path, monkeypatch)
    (tmp_path / "raw" / "dailymed" / SETID / "v7.xml").unlink()
    failed = {c.name for c in dm.verify(settings) if not c.ok}
    assert "raw XML kept" in failed


def test_verify_catches_stale_processed_file(tmp_path, monkeypatch):
    settings = _loaded(tmp_path, monkeypatch)
    path = tmp_path / "processed" / "dailymed" / f"{SETID}.json"
    doc = json.loads(path.read_text())
    doc["sections"][0]["text"] = "edited by hand"
    path.write_text(json.dumps(doc))
    failed = {c.name for c in dm.verify(settings) if not c.ok}
    assert "up to date with parser" in failed
