"""Shared fixtures: the spec's worked example (p18-19) as flags, evidence and a good answer."""

import json

import pytest
import yaml

from drug_safety.contracts import EvidenceChunk, Flag
from drug_safety.llm.drug_names import ALIASES_FILE, DrugNameFinder

SUMMARY = (
    "68-year-old male.\nPrescriptions: Plavix 75 mg; omeprazole 20 mg; metformin 500 mg.\n"
    "Labs: creatinine 2.45 mg/dL\nGenotypes: CYP2C19 *2/*2\nLifestyle: smoking: current."
)


def ev(cid, text):
    return EvidenceChunk(chunk_id=cid, doc_id="d-" + cid, source="dailymed", tier=1, text=text,
                         license="public domain")  # fmt: skip


@pytest.fixture
def flags():
    rows = yaml.safe_load(open("tests/gold/retrieval_flags.yaml"))["flags"]
    by_case = {r["case"]: r["flag"] for r in rows}
    cases = ["gene-clopidogrel-cyp2c19-pm", "ddi-clopidogrel-omeprazole", "lab-metformin-egfr"]
    return [Flag.model_validate(by_case[c]) for c in cases]


@pytest.fixture
def evidence():
    return {
        "gene-001": [
            ev(
                "cpic-clopidogrel-01",
                "CYP2C19 poor metabolizers form less active "
                "metabolite; consider an alternative antiplatelet.",
            ),  # fmt: skip
            ev(
                "label-clop-pgx",
                "Plavix at recommended doses forms less active "
                "metabolite in CYP2C19 poor metabolizers.",
            ),
        ],  # fmt: skip
        "ddi-001": [
            ev(
                "label-clop-ddi",
                "Avoid concomitant use of Plavix with omeprazole or "
                "esomeprazole. Omeprazole reduced active metabolite exposure by 45%.",
            )
        ],  # fmt: skip
        "lab-001": [
            ev(
                "label-met-renal",
                "Metformin is contraindicated in patients with an eGFR below 30 mL/min/1.73 m2.",
            )
        ],  # fmt: skip
    }


@pytest.fixture
def finder():
    """Offline finder: v1 aliases plus a few outside drugs (the real list is in data/)."""
    names = {n: n for n in ["prasugrel", "ticagrelor", "esomeprazole", "nexium", "morphine"]}
    for drug, aliases in yaml.safe_load(ALIASES_FILE.read_text())["aliases"].items():
        names |= {a.lower(): drug for a in [drug, *aliases]}
    return DrugNameFinder(names)


@pytest.fixture
def good():
    """A correct answer for all three flags (extends the spec's p19 excerpt)."""
    return {
        "summary": "All three prescriptions need review: genotype, a drug interaction and kidney "
        "function each affect one of them.",
        "flags_explained": [
            {"flag_id": "gene-001", "plain_explanation": "As a CYP2C19 poor metabolizer, this "
             "patient forms less of clopidogrel's active metabolite, and the guideline "
             "suggests an alternative antiplatelet.",
             "evidence_ids": ["cpic-clopidogrel-01"],
             "quotes": [{"evidence_id": "cpic-clopidogrel-01", "text": "CYP2C19 poor "
                         "metabolizers form less active metabolite; consider an alternative "
                         "antiplatelet."}]},
            {"flag_id": "ddi-001", "plain_explanation": "Omeprazole lowered the active "
             "metabolite exposure of clopidogrel by 45%.",
             "evidence_ids": ["label-clop-ddi"],
             "quotes": [{"evidence_id": "label-clop-ddi", "text": "Omeprazole reduced active "
                         "metabolite exposure by 45%."}]},
            {"flag_id": "lab-001", "plain_explanation": "The eGFR of 28 is below 30, where the "
             "metformin label says the drug is contraindicated.",
             "evidence_ids": ["label-met-renal"],
             "quotes": [{"evidence_id": "label-met-renal", "text": "Metformin is "
                         "contraindicated in patients with an eGFR below 30 mL/min/1.73 m2."}]},
        ],
        "suggestions_for_clinician_review": [
            {"text": "Consider reviewing the antiplatelet choice, as the guideline advises an "
             "alternative for poor metabolizers.", "flag_id": "gene-001",
             "evidence_ids": ["cpic-clopidogrel-01"]},
            {"text": "Consider reviewing metformin given reduced kidney function.",
             "flag_id": "lab-001", "evidence_ids": ["label-met-renal"]},
        ],
        "uncertainties": [],
    }  # fmt: skip


@pytest.fixture
def as_text():
    return json.dumps
