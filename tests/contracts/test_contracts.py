"""Contract tests: spec examples, the worked-example patient, and what must be rejected."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from drug_safety.contracts import (
    CONTRACTS_VERSION,
    AssessResponse,
    EvidenceChunk,
    Flag,
    Patient,
    Report,
    Severity,
    schemas,
)

SPEC = Path(__file__).parent / "spec_examples"


def spec(name):
    return json.loads((SPEC / name).read_text())


# ---------------------------------------------------------------- the spec's examples


def test_spec_report_examples_validate_verbatim():
    Report.model_validate(spec("report_p16.json"))
    Report.model_validate(spec("report_p19.json"))


def test_spec_flag_example_needs_gene_and_phenotype():
    """The spec's p14 flag has no gene/phenotype field. The draft requires them for gene_drug
    flags (the retriever builds its query from them), so the verbatim example is rejected with
    a clear message, and is valid once the two fields are added. Open point for Person A."""
    with pytest.raises(ValidationError, match="gene_drug flag needs gene, phenotype"):
        Flag.model_validate(spec("flag_p14.json"))
    flag = Flag.model_validate(
        spec("flag_p14.json") | {"gene": "CYP2C19", "phenotype": "poor metabolizer"}
    )
    assert flag.drug_ids() == ["clopidogrel"]
    q = flag.query_text()
    assert q.startswith("clopidogrel CYP2C19 poor metabolizer")  # spec RAG step 1 example
    assert "reduced activation" in q


def test_spec_worked_example_patient_p18():
    """68-year-old man: Plavix, omeprazole, metformin; creatinine 2.45 mg/dL; CYP2C19 *2/*2;
    smoker; vegetarian."""
    p = Patient.model_validate(
        {
            "patient_id": "worked-example",
            "age": 68,
            "sex": "male",
            "prescriptions": [{"name": "Plavix 75"}, {"name": "omeprazole"}, {"name": "metformin"}],
            "labs": [{"test": "creatinine", "value": 2.45, "unit": "mg/dL"}],
            "lifestyle": {"smoking": "current", "diet": {"type": "veg", "meals": ["dal and rice"]}},
            "genotypes": [{"gene": "CYP2C19", "diplotype": "*2/*2"}],
        }
    )
    assert p.prescriptions[0].name == "Plavix 75"  # raw name kept; normalisation resolves it


def worked_example_flags():
    """The three flags of the spec's worked example (p18), in the draft's full form."""
    return [
        Flag(
            flag_id="lab-001",
            flag_type="lab_drug",
            drug="metformin",
            severity="contraindicated",
            rule_id="lab_drug:egfr:lt30:metformin",
            reason="eGFR about 28 is below the metformin limit",
            source="label",
            lab={
                "measure": "egfr",
                "value": 28,
                "unit": "mL/min/1.73m2",
                "operator": "<",
                "threshold": 30,
            },
        ),
        Flag(
            flag_id="gene-001",
            flag_type="gene_drug",
            drug="clopidogrel",
            severity="major",
            rule_id="gene_drug:CYP2C19:poor_metabolizer:clopidogrel",
            reason="CYP2C19 poor metabolizer: reduced activation of clopidogrel",
            source="CPIC",
            gene="CYP2C19",
            phenotype="poor metabolizer",
        ),
        Flag(
            flag_id="ddi-001",
            flag_type="drug_drug",
            drug="clopidogrel",
            severity="major",
            rule_id="drug_drug:clopidogrel:omeprazole",
            reason="Omeprazole inhibits CYP2C19 and reduces clopidogrel's effect",
            source="DDInter",
            interacting_drug="omeprazole",
        ),
    ]


def test_worked_example_flags_and_retrieval_inputs():
    lab, gene, ddi = worked_example_flags()
    assert ddi.drug_ids() == ["clopidogrel", "omeprazole"]  # spec: either drug for interactions
    assert "egfr" in lab.query_text() and "metformin" in lab.query_text()


def test_worked_example_full_response():
    flags = worked_example_flags()
    chunk = EvidenceChunk(
        chunk_id="cpic-clopidogrel-01",
        doc_id="cpic:clopidogrel",
        source="cpic",
        tier=1,
        text="...",
        license="CC BY-SA 4.0",
    )
    r = AssessResponse(
        patient_id="worked-example",
        flags=flags,
        evidence={"gene-001": [chunk]},
        data_gaps=[],
        report=Report.model_validate(spec("report_p19.json")),
        report_source="llm",
    )
    assert "not medical advice" in r.disclaimer


# ---------------------------------------------------------------- what must be rejected


def _gene_flag(**change):
    base = spec("flag_p14.json") | {"gene": "CYP2C19", "phenotype": "poor metabolizer"}
    return base | change


@pytest.mark.parametrize(
    "change,message",
    [
        ({"severity": "severe"}, "severity"),  # not on the 4-level scale
        ({"flag_type": "gene"}, "flag_type"),  # unknown type
        ({"rule_id": "drug_drug:x"}, "must start with 'gene_drug:'"),  # type and rule disagree
        ({"flag_id": "G1"}, "must look like 'gene-001'"),
        ({"made_up_field": 1}, "Extra inputs are not permitted"),
    ],
)
def test_bad_flags_rejected(change, message):
    with pytest.raises(ValidationError, match=message):
        Flag.model_validate(_gene_flag(**change))


@pytest.mark.parametrize(
    "flag_type,rule_id,needs",
    [
        ("drug_drug", "drug_drug:a:b", "interacting_drug"),
        ("lab_drug", "lab_drug:egfr", "lab"),
        ("lifestyle_drug", "lifestyle_drug:smoking", "lifestyle"),
    ],
)
def test_each_flag_type_needs_its_trigger(flag_type, rule_id, needs):
    with pytest.raises(ValidationError, match=f"{flag_type} flag needs {needs}"):
        Flag.model_validate(spec("flag_p14.json") | {"flag_type": flag_type, "rule_id": rule_id})


def test_report_rejects_invented_fields_and_uncited_suggestions():
    good = spec("report_p19.json")
    with pytest.raises(ValidationError, match="Extra inputs"):
        Report.model_validate(good | {"dose_advice": "stop clopidogrel"})
    bad = json.loads(json.dumps(good))
    bad["suggestions_for_clinician_review"][0]["evidence_ids"] = []
    with pytest.raises(ValidationError, match="at least 1"):  # spec validator rule 5
        Report.model_validate(bad)
    with pytest.raises(ValidationError):
        Report.model_validate({k: v for k, v in good.items() if k != "uncertainties"})


def test_response_rules():
    lab, gene, ddi = worked_example_flags()
    with pytest.raises(ValidationError, match="sorted by severity"):
        AssessResponse(
            patient_id="p",
            flags=[gene, lab],
            evidence={},
            data_gaps=[],
            report=None,
            report_source="none",
        )
    with pytest.raises(ValidationError, match="unique"):
        AssessResponse(
            patient_id="p",
            flags=[gene, gene],
            evidence={},
            data_gaps=[],
            report=None,
            report_source="none",
        )
    with pytest.raises(ValidationError, match="evidence keys"):
        AssessResponse(
            patient_id="p",
            flags=[gene],
            evidence={"nope-001": []},
            data_gaps=[],
            report=None,
            report_source="none",
        )
    with pytest.raises(ValidationError, match="report must be present"):
        AssessResponse(
            patient_id="p",
            flags=[gene],
            evidence={},
            data_gaps=[],
            report=None,
            report_source="llm",
        )


def test_patient_rejects_bad_input():
    base = {"patient_id": "p", "age": 50, "sex": "female", "prescriptions": [{"name": "x"}]}
    Patient.model_validate(base)
    for bad in (
        {"age": 200},
        {"sex": "unknown"},
        {"prescriptions": []},
        {"lifestyle": {"smoking": "yes"}},
        {"weight": 70},  # the agreed field is weight_kg
        {"weight_kg": 0},
        {"weight_kg": 900},
        {"conditions": ["diabetes"]},  # only conditions that change a rule are allowed
        {"prescriptions": [{"name": "x", "status": "stopped"}]},
    ):
        with pytest.raises(ValidationError):
            Patient.model_validate(base | bad)


def test_agreed_optional_fields_a6_a8_a9():
    """Agreed with Person A 2026-11-14: all optional, so older patient files still load."""
    base = {"patient_id": "p", "age": 50, "sex": "female", "prescriptions": [{"name": "x"}]}
    old = Patient.model_validate(base)
    assert old.weight_kg is None and old.conditions == [] and old.prescriptions[0].status is None
    p = Patient.model_validate(base | {
        "prescriptions": [{"name": "Clozaril", "dose": "300 mg", "status": "ongoing"}],
        "weight_kg": 72.5, "conditions": ["benign_ethnic_neutropenia"]})  # fmt: skip
    assert p.prescriptions[0].status == "ongoing" and p.weight_kg == 72.5
    assert p.conditions == ["benign_ethnic_neutropenia"]
    assert CONTRACTS_VERSION == "0.1.0"


def test_severity_order():
    assert [s.value for s in sorted(Severity, key=lambda s: s.rank)] == [
        "contraindicated",
        "major",
        "moderate",
        "minor",
    ]


def test_json_schemas_up_to_date():
    assert schemas.main(["check"]) == 0, "run: python -m drug_safety.contracts.schemas write"


def test_contracts_depend_on_pydantic_only():
    import ast

    src = (Path(schemas.__file__).parent / "models.py").read_text()
    imported = {
        n.names[0].name.split(".")[0] if isinstance(n, ast.Import) else n.module.split(".")[0]
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Import | ast.ImportFrom)
    }
    assert imported <= {"__future__", "re", "enum", "typing", "pydantic"}, imported
