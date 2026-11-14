"""The prompt carries the spec's rules, each flag with only its own evidence, no patient ID."""

import json

from drug_safety.contracts import Patient
from drug_safety.llm.prompts import (
    SYSTEM,
    add_errors,
    build_messages,
    evidence_labels,
    patient_summary,
    resolve_labels,
    to_labels,
)

from .conftest import SUMMARY


def test_rules_and_evidence_per_flag(flags, evidence):
    system, user = build_messages(SUMMARY, flags, evidence)
    assert system["role"] == "system" and "Consider reviewing" in SYSTEM
    for rule in ("Use only the supplied", "Never add a drug", "uncertainties", "Return only JSON"):
        assert rule in SYSTEM
    blocks = user["content"].split("-----")
    assert len(blocks) == 3
    labels = evidence_labels(flags, evidence)
    assert list(labels.values()) == ["E1", "E2", "E3", "E4"]
    for block, f in zip(blocks, flags, strict=True):
        assert f"FLAG {f.flag_id}" in block
        own = {labels[c.chunk_id] for c in evidence[f.flag_id]}
        assert all(f"[{x}]" in block for x in own)
        assert not any(f"[{x}]" in block for x in set(labels.values()) - own)
    # chunk IDs never reach the model (they were a third of every answer)
    assert not any(cid in user["content"] for cid in labels)
    assert (
        '"flags_explained" must have exactly 3 entries, one for each of: gene-001, ddi-001, '
        "lab-001." in user["content"]
    )


def test_labels_resolve_to_chunk_ids(flags, evidence, good):
    labels = evidence_labels(flags, evidence)
    to_label = {v: k for k, v in labels.items()}
    inv = {cid: lab for lab, cid in to_label.items()}
    for item in good["flags_explained"] + good["suggestions_for_clinician_review"]:
        item["evidence_ids"] = [inv[x] for x in item["evidence_ids"]]
    good["flags_explained"][0]["evidence_ids"].append("E9")  # not a label: left for rule 3
    out = json.loads(resolve_labels("```json\n" + json.dumps(good) + "\n```", labels))
    assert out["flags_explained"][0]["evidence_ids"] == ["cpic-clopidogrel-01", "E9"]
    assert out["suggestions_for_clinician_review"][1]["evidence_ids"] == ["label-met-renal"]
    assert resolve_labels("not json", labels) == "not json"
    msg = "evidence ID 'x' was not supplied for gene-001 (allowed: ['cpic-clopidogrel-01'])"
    assert (
        to_labels(msg, labels) == "evidence ID 'x' was not supplied for gene-001 (allowed: ['E1'])"
    )


def test_same_chunk_under_two_flags_keeps_one_label(flags, evidence):
    evidence["ddi-001"] = evidence["ddi-001"] + evidence["gene-001"][:1]
    labels = evidence_labels(flags, evidence)
    assert labels["cpic-clopidogrel-01"] == "E1" and len(labels) == 4


def test_no_evidence_is_stated(flags):
    _, user = build_messages(SUMMARY, flags[:1], {})
    assert "No evidence was found for this flag." in user["content"]


def test_summary_has_no_patient_id():
    p = Patient.model_validate({
        "patient_id": "P-SECRET-42", "age": 68, "sex": "male",
        "prescriptions": [{"name": "Plavix", "dose": "75 mg"}],
        "labs": [{"test": "creatinine", "value": 2.45, "unit": "mg/dL"}],
        "genotypes": [{"gene": "CYP2C19", "diplotype": "*2/*2"}],
        "lifestyle": {"smoking": "current", "diet": {"type": "veg"}},
    })  # fmt: skip
    s = patient_summary(p)
    assert "P-SECRET-42" not in s
    assert "Plavix 75 mg" in s and "creatinine 2.45 mg/dL" in s and "CYP2C19 *2/*2" in s
    assert "Weight" not in s and "Conditions" not in s and "(" not in s.split("\n")[1]


def test_summary_shows_the_optional_fields_when_given():
    p = Patient.model_validate({
        "patient_id": "x", "age": 35, "sex": "male",
        "prescriptions": [{"name": "Clozaril", "dose": "300 mg", "status": "ongoing"}],
        "weight_kg": 72.5, "conditions": ["benign_ethnic_neutropenia"],
    })  # fmt: skip
    s = patient_summary(p)
    assert "Prescriptions: Clozaril 300 mg (ongoing)." in s
    assert "Weight: 72.5 kg" in s and "Conditions: benign ethnic neutropenia" in s


def test_retry_appends_answer_and_errors(flags, evidence):
    m = build_messages(SUMMARY, flags, evidence)
    r = add_errors(m, "{bad}", ["rule 2: flag x"])
    assert r[:2] == m and r[2] == {"role": "assistant", "content": "{bad}"}
    assert "rule 2: flag x" in r[3]["content"]
