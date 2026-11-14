"""Form rows <-> Patient, JSON upload, and the example patients. No Streamlit here, so it is
unit-tested directly.

    patient = to_patient(form)      # raises InputError with one readable line per problem
    form = to_form(patient)         # to fill the form from an example or an upload
    patient = parse_upload(data)    # a Patient JSON, or {"patient": {...}} as sent to the API
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

from drug_safety.contracts import Patient

EXAMPLES_FILE = Path(__file__).with_name("examples.yaml")
LAB_CODES_FILE = Path("scope/lab_codes.yaml")
SMOKING = [None, "never", "former", "current"]
ALCOHOL = [None, "none", "occasional", "regular", "heavy"]
DIET = [None, "veg", "non_veg"]
STATUS = ("new", "ongoing")  # Prescription.status (contract 0.1.0, A6)
CONDITIONS = {"benign_ethnic_neutropenia": "Benign ethnic neutropenia (Duffy-null)"}  # A9


class InputError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


class Form(BaseModel):
    """What the screen collects. Table rows are plain dicts, as st.data_editor returns them."""

    patient_id: str = ""
    age: int | None = None
    sex: str | None = None
    prescriptions: list[dict] = Field(default_factory=list)  # name, dose, frequency, status
    labs: list[dict] = Field(default_factory=list)  # test, value, unit, taken_at
    genotypes: list[dict] = Field(default_factory=list)  # gene, diplotype
    smoking: str | None = None
    alcohol: str | None = None
    diet_type: str | None = None
    meals: str = ""  # one meal per line
    weight_kg: float | None = None
    conditions: list[str] = Field(default_factory=list)


def _text(v) -> str | None:
    """A cell as clean text; empty, None and NaN (pandas' empty cell) become None."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    s = str(v).strip()
    return s or None


def _rows(rows: list[dict], cols: tuple[str, ...]) -> list[dict]:
    """Drop rows the user left completely empty (data_editor keeps them)."""
    clean = [{c: _text(r.get(c)) for c in cols} for r in rows]
    return [r for r in clean if any(r.values())]


def to_patient(form: Form) -> Patient:
    problems: list[str] = []
    rx = _rows(form.prescriptions, ("name", "dose", "frequency", "status"))
    for i, r in enumerate(rx, 1):
        if not r["name"]:
            problems.append(f"Prescription row {i}: the drug name is empty.")
        if r["status"] and r["status"] not in STATUS:
            problems.append(f"Prescription row {i}: status must be new or ongoing, or left empty.")
    if not rx:
        problems.append("Add at least one prescription.")
    labs = []
    for i, r in enumerate(_rows(form.labs, ("test", "value", "unit", "taken_at")), 1):
        if not r["test"] or not r["unit"] or r["value"] is None:
            problems.append(f"Lab row {i}: test, value and unit are all needed.")
            continue
        try:
            value = float(r["value"].replace(",", ""))
        except ValueError:
            problems.append(f"Lab row {i}: value {r['value']!r} is not a number.")
            continue
        labs.append({**r, "value": value})
    genes = _rows(form.genotypes, ("gene", "diplotype"))
    for i, r in enumerate(genes, 1):
        if not r["gene"] or not r["diplotype"]:
            problems.append(f"Genotype row {i}: gene and diplotype are both needed.")
    if not form.patient_id.strip():
        problems.append("Patient ID is empty.")
    if form.age is None:
        problems.append("Age is needed (the eGFR equation uses it).")
    if form.sex not in ("female", "male"):
        problems.append("Sex is needed (the eGFR equation uses it).")
    if form.weight_kg is not None and not 0 < form.weight_kg <= 400:
        problems.append("Weight must be between 0 and 400 kg, or left empty.")
    unknown = [c for c in form.conditions if c not in CONDITIONS]
    if unknown:
        problems.append(f"Unknown condition: {', '.join(unknown)}.")
    if problems:
        raise InputError(problems)
    meals = [m.strip() for m in form.meals.splitlines() if m.strip()]
    body = {
        "patient_id": form.patient_id.strip(),
        "age": form.age,
        "sex": form.sex,
        "prescriptions": [{k: v for k, v in r.items() if v} for r in rx],
        "labs": [{k: v for k, v in r.items() if v is not None} for r in labs],
        "genotypes": genes,
        "weight_kg": form.weight_kg,
        "conditions": form.conditions,
        "lifestyle": {
            "smoking": form.smoking,
            "alcohol": form.alcohol,
            "diet": {"type": form.diet_type, "meals": meals},
        },
    }
    try:
        return Patient.model_validate(body)
    except ValidationError as e:
        raise InputError(_problems(e)) from e


def to_form(p: Patient) -> Form:
    return Form(
        patient_id=p.patient_id,
        age=p.age,
        sex=p.sex,
        prescriptions=[x.model_dump() for x in p.prescriptions],
        labs=[x.model_dump() for x in p.labs],
        genotypes=[x.model_dump() for x in p.genotypes],
        smoking=p.lifestyle.smoking,
        alcohol=p.lifestyle.alcohol,
        diet_type=p.lifestyle.diet.type,
        meals="\n".join(p.lifestyle.diet.meals),
        weight_kg=p.weight_kg,
        conditions=list(p.conditions),
    )


def parse_upload(data: bytes) -> Patient:
    try:
        body = json.loads(data)
    except (ValueError, UnicodeDecodeError) as e:
        raise InputError([f"The file is not valid JSON ({e})."]) from e
    if isinstance(body, dict) and isinstance(body.get("patient"), dict):
        body = body["patient"]  # an AssessRequest
    try:
        return Patient.model_validate(body)
    except ValidationError as e:
        raise InputError(_problems(e)) from e


def _problems(e: ValidationError) -> list[str]:
    return [
        f"{'.'.join(str(x) for x in err['loc']) or 'patient'}: {err['msg']}" for err in e.errors()
    ]


class Example(BaseModel):
    id: str
    label: str
    patient: Patient


def load_examples(path: Path = EXAMPLES_FILE) -> list[Example]:
    return [Example.model_validate(x) for x in yaml.safe_load(path.read_text())["examples"]]


def lab_tests(path: Path = LAB_CODES_FILE) -> list[tuple[str, str, str]]:
    """(test name, unit, panel) the rules understand, for the form's help table."""
    if not path.exists():
        return []
    m = yaml.safe_load(path.read_text())["measures"]
    return [(k, v["unit"], v["panel"]) for k, v in m.items() if not v.get("derived")]
