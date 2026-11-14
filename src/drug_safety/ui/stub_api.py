"""Stand-in for Person A's API, so the interface can be built and tested before the API exists.
Delete once the real API runs; the interface needs no change (same endpoints, same contract).

    python -m drug_safety.ui.stub_api            # http://localhost:8080 (API_URL)
    python -m drug_safety.ui.stub_api --no-model # never call the model (template report)

What is real and what is not:
  - Flags and evidence: the frozen retrieval for the 10 example patients
    (data/processed/llm/cases.json, `make llm-cases`). There is no rule engine here, so only those
    patients, unchanged, get an answer; any other patient gets 404 saying so.
  - Report: the real LLM layer (llm.explain: Qwen at MODEL_URL, validator, retry, template
    fallback). With no model server it returns the template report and the "unavailable" notice,
    exactly as the real API will.
  - Data gaps: a rough guess from what is missing (no labs, no genotype, no smoking/alcohol).
    Person A's engine decides the real ones.
Endpoints as spec p17: POST /v1/assess, GET /v1/flags/{patient_id}, GET /health.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pydantic import ValidationError

from drug_safety.config import Settings, get_settings
from drug_safety.contracts import AssessRequest, AssessResponse, DataGap, Patient
from drug_safety.ui.patient_input import load_examples

log = logging.getLogger(__name__)

NOT_KNOWN = (
    "The stand-in API only answers the 10 example patients, unchanged (it has no rule engine). "
    "Load one from 'Load example'. Person A's API will assess any patient."
)


def data_gaps(p: Patient) -> list[DataGap]:
    gaps = []
    if not any(x.test.lower() == "creatinine" for x in p.labs):
        gaps.append(DataGap(kind="lab", message="No creatinine value: kidney (eGFR) rules skipped"))
    if not p.genotypes:
        gaps.append(DataGap(kind="genotype", message="No genotype given: gene-drug rules skipped"))
    if p.lifestyle.smoking is None and p.lifestyle.alcohol is None:
        gaps.append(
            DataGap(kind="lifestyle", message="Smoking and alcohol not given: those rules skipped")
        )
    return gaps


class Backend:
    """The stand-in's logic, separate from HTTP so tests call it directly."""

    def __init__(
        self,
        settings: Settings,
        llm: Callable[[list[dict]], str] | None = None,
        use_model: bool = True,
        finder=None,
    ):
        from drug_safety.llm.eval_llm import frozen_path, load_frozen

        self.settings = settings
        path = frozen_path(settings)
        if not path.exists():
            raise FileNotFoundError(f"{path} missing: run `make llm-cases` first")
        frozen = {c["case"]: c for c in load_frozen(path)}
        self.cases = {}  # patient JSON -> frozen case
        for ex in load_examples():
            if ex.id in frozen:
                self.cases[_key(ex.patient)] = frozen[ex.id]
        self._llm = llm
        self.use_model = use_model
        self._finder = finder

    def _explain_parts(self):
        from drug_safety.llm.drug_names import DrugNameFinder

        if self._finder is None:
            self._finder = DrugNameFinder.load(self.settings)
        if self._llm is None:
            if self.use_model:
                from drug_safety.llm.client import chat_model

                self._llm = chat_model(self.settings)
            else:
                self._llm = _no_model
        return self._llm, self._finder

    def assess(self, req: AssessRequest) -> AssessResponse | None:
        from drug_safety.llm.explain import explain

        case = self.cases.get(_key(req.patient))
        if case is None:
            return None
        flags = sorted(case["flags"], key=lambda f: f.severity.rank)
        report, source, notice = None, "none", None
        if req.use_llm:
            llm, finder = self._explain_parts()
            result = explain(case["summary"], flags, case["evidence"], llm, finder)
            report, source, notice = result.report, result.report_source, result.notice
        return AssessResponse(
            patient_id=req.patient.patient_id,
            flags=flags,
            evidence=case["evidence"],
            data_gaps=data_gaps(req.patient),
            report=report,
            report_source=source,
            notice=notice,
        )

    def flags(self, patient_id: str) -> list | None:
        for key, case in self.cases.items():
            if json.loads(key)["patient_id"] == patient_id:
                return [f.model_dump(mode="json", exclude_none=True) for f in case["flags"]]
        return None

    def health(self) -> tuple[int, dict]:
        from drug_safety.llm.client import ping

        model_ok, model_detail = (False, "turned off (--no-model)")
        if self.use_model and self._llm is None:
            model_ok, model_detail = ping(self.settings)
        elif self._llm is not None and self._llm is not _no_model:
            model_ok, model_detail = True, "test model"
        checks = {
            "api": {"ok": True, "detail": "stand-in API (ui/stub_api.py), not Person A's"},
            "model": {"ok": model_ok, "detail": model_detail},
            "database": {"ok": True, "detail": "not used: evidence is frozen"},
            "embedding": {"ok": True, "detail": "not used: evidence is frozen"},
        }
        # Spec: health reports reachability. A down model is not fatal: reports fall back.
        return 200, {"status": "ok" if model_ok else "degraded", "stub": True, "checks": checks}


def _no_model(messages):
    raise ConnectionError("model turned off in the stand-in API (--no-model)")


def _key(p: Patient) -> str:
    return p.model_dump_json()


def make_handler(backend: Backend):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802 (http.server's name)
            if self.path == "/health":
                self._send(*backend.health())
            elif self.path.startswith("/v1/flags/"):
                flags = backend.flags(self.path.removeprefix("/v1/flags/"))
                if flags is None:
                    self._send(404, {"detail": NOT_KNOWN})
                else:
                    self._send(200, {"flags": flags})
            else:
                self._send(404, {"detail": "Not Found"})

        def do_POST(self):  # noqa: N802
            if self.path != "/v1/assess":
                self._send(404, {"detail": "Not Found"})
                return
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                req = AssessRequest.model_validate_json(raw)
            except ValidationError as e:  # FastAPI's 422 shape
                self._send(422, {"detail": json.loads(e.json(include_url=False))})
                return
            resp = backend.assess(req)
            if resp is None:
                self._send(404, {"detail": NOT_KNOWN})
            else:
                self._send(200, resp.model_dump(mode="json"))

        def log_message(self, fmt, *args):
            log.info("%s %s", self.address_string(), fmt % args)

    return Handler


def serve(backend: Backend, host: str, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(backend))


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m drug_safety.ui.stub_api")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    p.add_argument("--no-model", action="store_true", help="never call MODEL_URL")
    args = p.parse_args(argv)
    server = serve(Backend(settings, use_model=not args.no_model), args.host, args.port)
    print(f"Stand-in API at http://{args.host}:{args.port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
