"""Curation checks catch each kind of mistake (one planted mistake per test).

Runs against the real chunk file and curated YAML; skipped if the chunks haven't been built.
"""

import copy
from pathlib import Path

import pytest
import yaml

from drug_safety.config import get_settings
from drug_safety.curation import rules

CHUNKS = Path("data/processed/chunks/dailymed.jsonl")
pytestmark = pytest.mark.skipif(not CHUNKS.exists(), reason="run make chunk-labels first")


def _rows(kind):
    return yaml.safe_load(open(rules.FILES[kind]))["rules"]


def failures(tmp_path, monkeypatch, kind, rule_id, change):
    """Apply `change` to one row in a temp copy of the YAML, run verify, return failed checks."""
    files = {}
    for k, path in rules.FILES.items():
        rows = copy.deepcopy(_rows(k))
        if k == kind:
            row = next(r for r in rows if r["rule_id"] == rule_id)
            change(row)
        files[k] = tmp_path / path.name
        files[k].write_text(yaml.safe_dump({"rules": rows}, allow_unicode=True))
    monkeypatch.setattr(rules, "FILES", files)
    return {(c.rule_id, c.name) for c in rules.verify(get_settings()) if not c.ok}


METFORMIN = "lab_drug:egfr:lt30:metformin"
CLOZ_SMOKE = "lifestyle_drug:smoking:current:clozapine"


def test_real_files_pass():
    assert [c for c in rules.verify(get_settings()) if not c.ok] == []


def test_edited_quote_fails(tmp_path, monkeypatch):
    def edit(r):
        r["evidence"][0]["quote"] = r["evidence"][0]["quote"].replace("below 30", "below 25")

    f = failures(tmp_path, monkeypatch, "lab", METFORMIN, edit)
    assert (METFORMIN, "quote 1: text is in its chunk") in f


def test_quote_from_another_drugs_label_fails(tmp_path, monkeypatch):
    other = next(r for r in _rows("lifestyle") if r["drug"] == "theophylline")["evidence"][0]

    def swap(r):
        r["evidence"] = [other]

    f = failures(tmp_path, monkeypatch, "lifestyle", CLOZ_SMOKE, swap)
    assert (CLOZ_SMOKE, "quote 1: chunk is from clozapine's own label") in f


def test_wrong_threshold_fails(tmp_path, monkeypatch):
    def bump(r):
        r["threshold"] = 35

    f = failures(tmp_path, monkeypatch, "lab", METFORMIN, bump)
    assert (METFORMIN, "threshold equals the label number in the canonical unit") in f


def test_label_number_not_in_quote_fails(tmp_path, monkeypatch):
    def bump(r):
        r["label_threshold"] = "35"

    f = failures(tmp_path, monkeypatch, "lab", METFORMIN, bump)
    assert (METFORMIN, "label number appears in a quote") in f


def test_severity_contradicting_phrase_fails(tmp_path, monkeypatch):
    def soften(r):
        r["severity"] = "minor"  # the quote says "contraindicated"

    f = failures(tmp_path, monkeypatch, "lab", METFORMIN, soften)
    assert (METFORMIN, "severity matches the phrase table") in f


def test_severity_override_needs_reason(tmp_path, monkeypatch):
    def override(r):
        r["severity"] = "major"
        r["severity_override_reason"] = "pharmacist judgement: test"

    f = failures(tmp_path, monkeypatch, "lab", METFORMIN, override)
    assert (METFORMIN, "severity matches the phrase table") not in f


def test_basis_not_in_quote_fails(tmp_path, monkeypatch):
    def basis(r):
        r["severity_basis"] = "avoid"
        r["severity"] = "major"

    f = failures(tmp_path, monkeypatch, "lab", METFORMIN, basis)
    assert (METFORMIN, "severity basis phrase appears in a quote (spacing ignored)") in f


def test_operator_contradicting_wording_fails(tmp_path, monkeypatch):
    rid = "lab_drug:egfr:gt30:metformin"

    def flip(r):  # the quote says "below 30"
        r["operator"] = ">"
        r["rule_id"] = rid

    f = failures(tmp_path, monkeypatch, "lab", METFORMIN, flip)
    assert (rid, "quote wording agrees with the operator (above / greater than)") in f


def test_proxy_without_note_fails(tmp_path, monkeypatch):
    rid = "lab_drug:egfr:lt30:tramadol"

    def drop(r):
        r.pop("proxy_note")

    f = failures(tmp_path, monkeypatch, "lab", rid, drop)
    assert (rid, "proxy measure is explained (proxy_note)") in f


def test_approved_without_reviewer_fails(tmp_path, monkeypatch):
    def approve(r):
        r["status"] = "approved"

    f = failures(tmp_path, monkeypatch, "lab", METFORMIN, approve)
    assert (METFORMIN, "approved rows name a reviewer and a date") in f


def test_lifestyle_value_outside_contract_fails(tmp_path, monkeypatch):
    def bad(r):
        r["factor_values"] = ["daily"]
        r["rule_id"] = "lifestyle_drug:smoking:daily:clozapine"

    f = failures(tmp_path, monkeypatch, "lifestyle", CLOZ_SMOKE, bad)
    assert (
        "lifestyle_drug:smoking:daily:clozapine",
        "factor values are allowed by the Patient contract / diet tag list",
    ) in f


def test_rule_for_non_users_fails(tmp_path, monkeypatch):
    def never(r):
        r["factor_values"] = ["never"]
        r["rule_id"] = "lifestyle_drug:smoking:never:clozapine"

    f = failures(tmp_path, monkeypatch, "lifestyle", CLOZ_SMOKE, never)
    assert (
        "lifestyle_drug:smoking:never:clozapine",
        "no rule fires for a non-user ('never' smoker, 'none' alcohol)",
    ) in f


def test_export_writes_approved_rows_only(tmp_path, monkeypatch):
    real_context = rules.load_context(get_settings())
    monkeypatch.setattr(rules, "load_context", lambda _s: real_context)
    s = get_settings().model_copy(update={"data_dir": tmp_path})
    approved = sum(r["status"] == "approved" for k in rules.FILES for r in _rows(k))
    assert rules.export(s) == {"lab_drug_rules": approved, "lifestyle_drug": 0}

    # Approve one lifestyle row (reviewer and date set): it, and only it, is exported,
    # once per factor value.
    rows = copy.deepcopy(_rows("lifestyle"))
    row = next(
        r for r in rows if r["rule_id"] == "lifestyle_drug:alcohol:occasional-regular-heavy:codeine"
    )
    row |= {"status": "approved", "reviewed_by": "test", "reviewed_on": "2026-11-11"}
    path = tmp_path / "lifestyle_drug.yaml"
    path.write_text(yaml.safe_dump({"rules": rows}, allow_unicode=True))
    monkeypatch.setattr(rules, "FILES", {"lab": rules.FILES["lab"], "lifestyle": path})
    assert rules.export(s)["lifestyle_drug"] == 3  # occasional, regular, heavy
    import csv

    with open(tmp_path / "processed" / "curation" / "lifestyle_drug.csv") as f:
        exported = list(csv.DictReader(f))
    assert [(r["drug"], r["factor_value"]) for r in exported] == [
        ("codeine", "occasional"),
        ("codeine", "regular"),
        ("codeine", "heavy"),
    ]
    assert exported[0]["reviewed_by"] == "test" and exported[0]["evidence_chunk_id"]


def test_missing_coverage_note_fails(tmp_path, monkeypatch):
    """A drug with no rows and no written reason fails (spec: empty cells are reviewed)."""
    notes = yaml.safe_load(open(rules.CURATION / "coverage_notes.yaml"))
    del notes["lab"]["warfarin"]
    (tmp_path / "coverage_notes.yaml").write_text(yaml.safe_dump(notes))
    for name in ("severity_rules.yaml", "units.yaml", "diet_tags.yaml"):
        (tmp_path / name).write_text((rules.CURATION / name).read_text())
    monkeypatch.setattr(rules, "CURATION", tmp_path)
    failed = {c.name for c in rules.verify(get_settings()) if not c.ok}
    assert "lab: warfarin has rows or a coverage note" in failed


def test_approved_row_with_open_review_flag_needs_answer(tmp_path, monkeypatch):
    rid = "lab_drug:anc:lt1_5:clozapine"

    def approve(r):
        r |= {"status": "approved", "reviewed_by": "x", "reviewed_on": "2026-11-11"}

    f = failures(tmp_path, monkeypatch, "lab", rid, approve)
    assert (rid, "approved rows have their review flags answered (review_answer)") in f
