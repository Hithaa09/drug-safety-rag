import json

import httpx
import pytest

from drug_safety.ui.api_client import ApiClient, ApiError


def client(handler):
    return ApiClient("http://api.test/", transport=httpx.MockTransport(handler))


def test_assess_sends_request_and_checks_contract(response, example):
    sent = {}

    def handler(req):
        sent.update(path=req.url.path, body=json.loads(req.content))
        return httpx.Response(200, json=response().model_dump(mode="json"))

    r = client(handler).assess(example.patient, use_llm=False)
    assert sent["path"] == "/v1/assess"
    assert sent["body"]["use_llm"] is False
    assert sent["body"]["patient"]["patient_id"] == "p01"
    assert [f.flag_id for f in r.flags] == ["lab-001", "gene-001", "ddi-001"]


@pytest.mark.parametrize("status, body, expected", [
    (422, {"detail": [{"loc": ["body", "patient", "age"], "msg": "too large"}]},
     "(422): body.patient.age: too large"),
    (404, {"detail": "only the examples"}, "(404): only the examples"),
    (500, "Internal Server Error", "(500): Internal Server Error"),
])  # fmt: skip
def test_refusals_are_readable(example, status, body, expected):
    def handler(req):
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)

    with pytest.raises(ApiError, match=expected.replace("(", r"\(").replace(")", r"\)")):
        client(handler).assess(example.patient)


def test_response_not_matching_contract(response, example):
    body = response().model_dump(mode="json")
    del body["data_gaps"]
    with pytest.raises(ApiError, match="does not match the shared contract.*data_gaps"):
        client(lambda req: httpx.Response(200, json=body)).assess(example.patient)


def test_not_json(example):
    with pytest.raises(ApiError, match="shared contract"):
        client(lambda req: httpx.Response(200, text="<html>")).assess(example.patient)


def test_api_down_and_timeout(example):
    def down(req):
        raise httpx.ConnectError("refused")

    def slow(req):
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ApiError, match="Cannot reach the API at http://api.test"):
        client(down).assess(example.patient)
    with pytest.raises(ApiError, match="did not answer in time"):
        client(slow).assess(example.patient)


def test_health():
    ok, body = client(lambda req: httpx.Response(200, json={"status": "ok"})).health()
    assert ok and body == {"status": "ok"}
    ok, body = client(lambda req: httpx.Response(503, json={"status": "down"})).health()
    assert not ok and body == {"status": "down"}

    def down(req):
        raise httpx.ConnectError("refused")

    ok, body = client(down).health()
    assert not ok and "Cannot reach" in body
