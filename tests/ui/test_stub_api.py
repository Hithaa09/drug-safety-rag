import json

import httpx

from drug_safety.contracts import AssessResponse, Lifestyle, Patient, Prescription
from drug_safety.llm.explain import NOTICE_DOWN
from drug_safety.ui.stub_api import NOT_KNOWN, data_gaps


def post(url, patient, use_llm=True):
    return httpx.post(f"{url}/v1/assess", json={"patient": patient.model_dump(mode="json"),
                                                 "use_llm": use_llm})  # fmt: skip


def test_example_patient_without_model_gets_template(start_stub, example):
    r = post(start_stub(), example.patient)
    assert r.status_code == 200
    resp = AssessResponse.model_validate(r.json())
    assert [f.flag_id for f in resp.flags] == ["lab-001", "gene-001", "ddi-001"]
    assert resp.report_source == "template" and resp.notice == NOTICE_DOWN
    assert resp.data_gaps == []  # p01 has creatinine, a genotype and smoking


def test_example_patient_with_model_answer(start_stub, example, good):
    resp = AssessResponse.model_validate(
        post(start_stub(llm=lambda m: json.dumps(good)), example.patient).json()
    )
    assert resp.report_source == "llm" and resp.notice is None
    assert resp.report.summary == good["summary"]


def test_use_llm_false(start_stub, example):
    resp = AssessResponse.model_validate(post(start_stub(), example.patient, use_llm=False).json())
    assert resp.report is None and resp.report_source == "none"


def test_changed_patient_is_refused_not_guessed(start_stub, example):
    changed = example.patient.model_copy(update={"age": 69})
    r = post(start_stub(), changed)
    assert r.status_code == 404 and r.json()["detail"] == NOT_KNOWN


def test_invalid_body_gets_fastapi_style_422(start_stub):
    r = httpx.post(f"{start_stub()}/v1/assess", json={"patient": {"age": 5}})
    assert r.status_code == 422
    assert {"loc", "msg"} <= set(r.json()["detail"][0])


def test_health_and_flags(start_stub):
    url = start_stub()
    h = httpx.get(f"{url}/health").json()
    assert h["stub"] is True and h["status"] == "degraded"
    assert set(h["checks"]) == {"api", "model", "database", "embedding"}
    assert [f["flag_id"] for f in httpx.get(f"{url}/v1/flags/p01").json()["flags"]] == [
        "lab-001",
        "gene-001",
        "ddi-001",
    ]
    assert httpx.get(f"{url}/v1/flags/nobody").status_code == 404


def test_data_gaps_guess():
    p = Patient(patient_id="x", age=40, sex="male", prescriptions=[Prescription(name="x")],
                lifestyle=Lifestyle())  # fmt: skip
    assert [g.kind for g in data_gaps(p)] == ["lab", "genotype", "lifestyle"]
