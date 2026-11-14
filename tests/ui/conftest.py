"""Shared fixtures: the spec's worked example (p18-19) as an API response, and a stand-in API
running on a free port with its frozen data in a temporary folder (no database, no model)."""

import json
import threading

import pytest
import yaml

from drug_safety.config import Settings, get_settings
from drug_safety.contracts import AssessResponse, EvidenceChunk, Flag, Report
from drug_safety.llm.drug_names import ALIASES_FILE, DrugNameFinder
from drug_safety.llm.prompts import patient_summary
from drug_safety.ui.patient_input import load_examples
from drug_safety.ui.stub_api import Backend, serve

SETID = "de8b0b67-eb25-4684-83b5-7ad785314227"


def ev(cid, text, doc_id=f"dailymed:{SETID}", source="dailymed", tier=1):
    return EvidenceChunk(chunk_id=cid, doc_id=doc_id, source=source, tier=tier, text=text,
                         title="PLAVIX (CLOPIDOGREL)", heading="12.5 Pharmacogenomics",
                         license="DailyMed (NLM) SPL")  # fmt: skip


@pytest.fixture
def flags():
    rows = yaml.safe_load(open("tests/gold/retrieval_flags.yaml"))["flags"]
    by_case = {r["case"]: r["flag"] for r in rows}
    cases = ["lab-metformin-egfr", "gene-clopidogrel-cyp2c19-pm", "ddi-clopidogrel-omeprazole"]
    return [Flag.model_validate(by_case[c]) for c in cases]  # severity order


@pytest.fixture
def evidence():
    return {
        "gene-001": [
            ev("cpic-clopidogrel-01", "CYP2C19 poor metabolizers form less active metabolite; "
               "consider an alternative antiplatelet.", doc_id="pmc:PMC3159941", source="pmc",
               tier=3),
            ev("label-clop-pgx", "Plavix at recommended doses forms less active metabolite in "
               "CYP2C19 poor metabolizers."),
        ],
        "ddi-001": [
            ev("label-clop-ddi", "Avoid concomitant use of Plavix with omeprazole or "
               "esomeprazole. Omeprazole reduced active metabolite exposure by 45%."),
        ],
        "lab-001": [
            ev("label-met-renal", "Metformin is contraindicated in patients with an eGFR below "
               "30 mL/min/1.73 m2."),
        ],
    }  # fmt: skip


@pytest.fixture
def good():
    """A correct answer for all three flags (extends the spec's p19 excerpt)."""
    return {
        "summary": "All three prescriptions need review: genotype, a drug interaction and kidney "
        "function each affect one of them.",
        "flags_explained": [
            {"flag_id": "gene-001", "plain_explanation": "Clopidogrel must be activated by "
             "CYP2C19. This patient's genotype produces little of the active form, so the "
             "guideline suggests an alternative antiplatelet.",
             "evidence_ids": ["cpic-clopidogrel-01"]},
            {"flag_id": "ddi-001", "plain_explanation": "Omeprazole lowers the active metabolite "
             "of clopidogrel by about 45%, which may weaken its effect.",
             "evidence_ids": ["label-clop-ddi"]},
            {"flag_id": "lab-001", "plain_explanation": "The eGFR of 28 is below 30, where the "
             "metformin label says the drug is contraindicated.",
             "evidence_ids": ["label-met-renal"]},
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
def response(flags, evidence, good):
    def make(report=good, source="llm", notice=None, ev=evidence, data_gaps=()):
        return AssessResponse(
            patient_id="p01",
            flags=flags,
            evidence=ev,
            data_gaps=list(data_gaps),
            report=None if report is None else Report.model_validate(report),
            report_source="none" if report is None else source,
            notice=notice,
        )

    return make


@pytest.fixture
def finder():
    names = {n: n for n in ["prasugrel", "ticagrelor", "esomeprazole", "nexium", "morphine"]}
    for drug, aliases in yaml.safe_load(ALIASES_FILE.read_text())["aliases"].items():
        names |= {a.lower(): drug for a in [drug, *aliases]}
    return DrugNameFinder(names)


@pytest.fixture
def example():
    return load_examples()[0]  # p01, the worked example


@pytest.fixture
def stub_settings(tmp_path, flags, evidence, example):
    """Frozen data for p01 only, in a temporary data folder."""
    frozen = tmp_path / "processed" / "llm" / "cases.json"
    frozen.parent.mkdir(parents=True)
    case = {
        "case": example.id,
        "summary": patient_summary(example.patient),
        "flags": [f.model_dump(mode="json", exclude_none=True) for f in flags],
        "evidence": {k: [c.model_dump(mode="json") for c in v] for k, v in evidence.items()},
    }
    frozen.write_text(json.dumps({"cases": [case]}))
    return Settings(data_dir=tmp_path, _env_file=None)


@pytest.fixture
def start_stub(stub_settings, finder):
    """start_stub(llm=None) -> base URL. llm=None means no model (template report)."""
    servers = []

    def start(llm=None):
        backend = Backend(stub_settings, llm=llm, use_model=False, finder=finder)
        srv = serve(backend, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_port}"

    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


@pytest.fixture
def api_url(monkeypatch):
    """Point the app at a URL (get_settings is cached, so clear it)."""

    def point(url):
        monkeypatch.setenv("API_URL", url)
        get_settings.cache_clear()

    yield point
    get_settings.cache_clear()
