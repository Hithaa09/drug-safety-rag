"""Acceptance checks for the screen: all 10 example patients through the stand-in API (HTTP) and
the real app (Streamlit AppTest, headless), checked against spec p17.

    python -m drug_safety.ui.verify       # needs data/processed/llm/cases.json (`make llm-cases`)

No model is called (template reports); the AI-written path is covered by tests/ui/test_app.py.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from drug_safety.config import get_settings
from drug_safety.contracts import Patient
from drug_safety.ui import view
from drug_safety.ui.api_client import ApiClient
from drug_safety.ui.patient_input import load_examples, parse_upload

APP = str(Path(__file__).with_name("app.py"))


def run_example(at_cls, index: int):
    at = at_cls.from_file(APP, default_timeout=60).run()
    at.sidebar.selectbox[0].select(index).run()
    next(b for b in at.button if b.label == "Load example").click().run()
    next(b for b in at.button if b.label == "Assess").click().run()
    return at


def main() -> int:
    import os

    from streamlit.testing.v1 import AppTest

    from drug_safety.ui.stub_api import Backend, serve

    settings = get_settings()
    srv = serve(Backend(settings, use_model=False), "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}"
    os.environ["API_URL"] = url
    get_settings.cache_clear()
    client = ApiClient(url)

    checks: list[tuple[str, bool, str]] = []
    examples = load_examples()
    checks.append(("10 synthetic example patients load", len(examples) == 10, str(len(examples))))
    bad: dict[str, list[str]] = {}

    def fail(what, ex_id):
        bad.setdefault(what, []).append(ex_id)

    for i, ex in enumerate(examples):
        p: Patient = ex.patient
        if parse_upload(p.model_dump_json().encode()) != p:
            fail("upload", ex.id)
        r = client.assess(p, use_llm=True)
        if not all(view.source_url(c) for v in r.evidence.values() for c in v):
            fail("links", ex.id)
        at = run_example(AppTest, i)
        if at.exception:
            fail("exception", ex.id)
            continue
        if not at.warning or not at.warning[0].value.startswith("**Decision support"):
            fail("banner", ex.id)
        table = at.dataframe[-1].value
        if list(table["Flag"]) != [f.flag_id for f in r.flags] or list(table["Severity"]) != [
            row["Severity"] for row in view.flag_table(r)
        ]:
            fail("table", ex.id)
        if [f.severity.rank for f in r.flags] != sorted(f.severity.rank for f in r.flags):
            fail("table", ex.id)
        n_chunks = sum(len(v) for v in r.evidence.values())
        if len([e for e in at.expander if e.label.startswith("[")]) != n_chunks:
            fail("evidence", ex.id)
        if r.notice and not any(w.value.startswith("AI summary unavailable") for w in at.warning):
            fail("notice", ex.id)
        notes = [x.value for x in at.info if x.value.startswith("Note (")]
        if len(notes) != len(r.data_gaps):
            fail("gaps", ex.id)
        subs = [s.value for s in at.subheader]
        if not {"Summary", "Suggestions for clinician review"} <= set(subs):
            fail("summary", ex.id)
    srv.shutdown()

    for key, name in [
        ("exception", "the screen renders every example without an error"),
        ("banner", "fixed decision-support banner is shown first"),
        ("table", "flag table lists every flag, sorted by severity"),
        ("summary", "summary and suggestions shown"),
        ("evidence", "every retrieved passage has its own expandable section"),
        ("links", "every passage links to its source document"),
        ("notice", "'AI summary unavailable' notice shown with the template report"),
        ("gaps", "every data gap shown as a note"),
        ("upload", "each patient survives the JSON upload unchanged"),
    ]:
        checks.append((name, key not in bad, ", ".join(bad.get(key, []))))

    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + ("" if ok or not detail else f": {detail}"))
    failed = [c for c in checks if not c[1]]
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed.")
    print("RESULT:", "PASS" if not failed else "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
