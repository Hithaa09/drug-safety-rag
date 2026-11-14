import json
import math

import pytest
import yaml

from drug_safety.contracts import Patient
from drug_safety.ui.patient_input import (
    Form,
    InputError,
    lab_tests,
    load_examples,
    parse_upload,
    to_form,
    to_patient,
)


def base(**kw):
    f = dict(patient_id="x1", age=60, sex="female", prescriptions=[{"name": "warfarin"}])
    return Form(**{**f, **kw})


def problems(form):
    with pytest.raises(InputError) as e:
        to_patient(form)
    return e.value.problems


def test_examples_are_the_llm_gold_patients():
    gold = yaml.safe_load(open("tests/gold/llm_cases.yaml"))["patients"]
    ex = load_examples()
    assert [e.id for e in ex] == [g["id"] for g in gold]
    assert [e.patient for e in ex] == [Patient.model_validate(g["patient"]) for g in gold]


@pytest.mark.parametrize("ex", load_examples(), ids=lambda e: e.id)
def test_form_round_trip(ex):
    assert to_patient(to_form(ex.patient)) == ex.patient


def test_empty_rows_and_blank_cells_dropped():
    p = to_patient(base(
        prescriptions=[{"name": " Plavix ", "dose": "75 mg", "frequency": None},
                       {"name": None, "dose": "", "frequency": math.nan}],
        labs=[{"test": "creatinine", "value": "2.45", "unit": "mg/dL", "taken_at": ""},
              {"test": "", "value": None, "unit": None, "taken_at": None}],
        genotypes=[{"gene": "", "diplotype": None}],
    ))  # fmt: skip
    assert [(x.name, x.dose, x.frequency) for x in p.prescriptions] == [("Plavix", "75 mg", None)]
    assert [(x.test, x.value, x.unit, x.taken_at) for x in p.labs] == [
        ("creatinine", 2.45, "mg/dL", None)
    ]
    assert p.genotypes == []


def test_number_with_thousands_comma():
    p = to_patient(base(labs=[{"test": "wbc", "value": "1,200", "unit": "10^3/uL"}]))
    assert p.labs[0].value == 1200


def test_meals_one_per_line_and_lifestyle():
    p = to_patient(base(meals="spinach salad\n\n  kale soup ", smoking="current", diet_type="veg"))
    assert p.lifestyle.diet.meals == ["spinach salad", "kale soup"]
    assert p.lifestyle.smoking == "current" and p.lifestyle.diet.type == "veg"
    assert p.lifestyle.alcohol is None


def test_all_problems_reported_at_once():
    got = problems(Form(
        patient_id=" ", age=None, sex=None,
        prescriptions=[{"name": "", "dose": "5 mg"}],
        labs=[{"test": "ALT", "value": "high", "unit": "U/L"}, {"test": "AST", "value": "40"}],
        genotypes=[{"gene": "CYP2C9", "diplotype": ""}],
    ))  # fmt: skip
    assert got == [
        "Prescription row 1: the drug name is empty.",
        "Lab row 1: value 'high' is not a number.",
        "Lab row 2: test, value and unit are all needed.",
        "Genotype row 1: gene and diplotype are both needed.",
        "Patient ID is empty.",
        "Age is needed (the eGFR equation uses it).",
        "Sex is needed (the eGFR equation uses it).",
    ]


def test_no_prescription():
    assert "Add at least one prescription." in problems(base(prescriptions=[{"name": None}]))


def test_contract_limits_still_apply():
    assert any("age" in p for p in problems(base(age=130)))


def test_upload_patient_or_request():
    p = load_examples()[0].patient
    raw = p.model_dump_json().encode()
    assert parse_upload(raw) == p
    assert parse_upload(json.dumps({"patient": p.model_dump(), "use_llm": False}).encode()) == p


def test_upload_errors_are_readable():
    with pytest.raises(InputError, match="not valid JSON"):
        parse_upload(b"{not json")
    with pytest.raises(InputError) as e:
        body = {"patient_id": "a", "age": 50, "sex": "male", "prescriptions": [{"name": "x"}]}
        parse_upload(json.dumps({**body, "weight": 80}).encode())
    assert e.value.problems == ["weight: Extra inputs are not permitted"]
    with pytest.raises(InputError) as e:
        parse_upload(b"[]")
    assert e.value.problems


def test_lab_tests_come_from_scope():
    names = [n for n, _, _ in lab_tests()]
    assert {"creatinine", "alt", "platelets", "anc"} <= set(names)
    assert "egfr" not in names  # calculated, not entered


def test_agreed_optional_fields_round_trip():
    """Contract 0.1.0 (Person A, 2026-11-14): status, weight and conditions."""
    p = to_patient(base(prescriptions=[{"name": "Clozaril", "dose": "300 mg", "status": "ongoing"},
                                       {"name": "x", "status": math.nan}],
                        weight_kg=72.5, conditions=["benign_ethnic_neutropenia"]))  # fmt: skip
    assert [x.status for x in p.prescriptions] == ["ongoing", None]
    assert p.weight_kg == 72.5 and p.conditions == ["benign_ethnic_neutropenia"]
    assert to_patient(to_form(p)) == p


def test_optional_fields_bad_values_are_explained():
    got = problems(base(prescriptions=[{"name": "x", "status": "stopped"}], weight_kg=0.0,
                        conditions=["diabetes"]))  # fmt: skip
    assert got == [
        "Prescription row 1: status must be new or ongoing, or left empty.",
        "Weight must be between 0 and 400 kg, or left empty.",
        "Unknown condition: diabetes.",
    ]
