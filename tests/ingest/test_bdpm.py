"""The acenocoumarol label translation: French kept word for word, only reviewed passages used."""

import pytest

from drug_safety.config import get_settings
from drug_safety.ingest.bdpm import (
    french_mismatches,
    load_translations,
    page_text,
    raw_path,
    updated_on_page,
)
from drug_safety.ingest.rxclass import out_path as rxclass_out
from drug_safety.rag.chunk_bdpm import chunk_text, chunks, sheet

T = load_translations()
SETTINGS = get_settings()
PAGE = raw_path(SETTINGS, T.source.cis)
needs_page = pytest.mark.skipif(not PAGE.exists(), reason="run `make fetch-bdpm`")


def test_page_text_and_update_date():
    html = "<p class=x>ANSM - Mis à jour le : 20/06/2025</p><p>Hypertension&nbsp; <b>sévère</b></p>"
    page = page_text(html)
    assert page.splitlines() == ["ANSM - Mis à jour le : 20/06/2025", "Hypertension sévère"]
    assert updated_on_page(page) == "2025-06-20"


@needs_page
def test_french_is_on_the_page_and_one_changed_word_is_caught():
    page = page_text(PAGE.read_text(encoding="utf-8", errors="replace"))
    assert french_mismatches(T, page) == []
    t = T.model_copy(deep=True)
    t.passages[0].french = t.passages[0].french.replace("sévère", "modérée", 1)
    assert [m.split(":")[0] for m in french_mismatches(t, page)] == ["contraindications"]


def test_translation_shape():
    for p in T.passages:
        assert len(p.french.splitlines()) == len(p.english.splitlines()), p.id
        assert ("[…]" in p.french) == ("[…]" in p.english), p.id
        assert not p.reviewed or (p.reviewed_by and p.reviewed_on), p.id


@pytest.mark.skipif(not rxclass_out(SETTINGS).exists(), reason="run `make fetch-classes`")
def test_only_reviewed_passages_become_chunks():
    count = lambda s: len(s.split())  # noqa: E731
    t = T.model_copy(deep=True)
    for p in t.passages:
        p.reviewed = False
    assert chunks(SETTINGS, t, count) == []
    bleeding = next(p for p in t.passages if p.id == "interactions-bleeding")
    bleeding.reviewed, bleeding.reviewed_by, bleeding.reviewed_on = True, "Test", "2026-11-14"
    (c,) = chunks(SETTINGS, t, count)
    assert c.text.startswith("acenocoumarol | SINTROM 4 mg")  # own-label rule reads this
    assert {"acenocoumarol", "citalopram", "sertraline", "amitriptyline", "tramadol"} <= set(
        c.drugs
    )
    assert "reviewed by Test on 2026-11-14" in c.license


def test_sheet_shows_review_state():
    text = sheet(T)
    assert text.count("not reviewed") == sum(not p.reviewed for p in T.passages)
    assert chunk_text(T, T.passages[0]).splitlines()[0].endswith("| 4.3 Contraindications")
