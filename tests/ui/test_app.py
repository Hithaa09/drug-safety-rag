"""The screen itself, run headless with Streamlit's AppTest against the stand-in API."""

import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from drug_safety.ui.view import DISCLAIMER, md

APP = str(Path(__file__).parents[2] / "src/drug_safety/ui/app.py")


def run_app():
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception, at.exception
    return at


def button(at, label):
    return next(b for b in at.button if b.label == label)


def load_example(at, index=0):
    at.sidebar.selectbox[0].select(index).run()
    button(at, "Load example").click().run()
    return at


def assess(at, use_llm=True):
    at.checkbox[0].set_value(use_llm)
    button(at, "Assess").click().run()
    assert not at.exception, at.exception
    return at


def evidence_labels(at):
    return [e.label for e in at.expander if e.label.startswith("[")]


def markdown(at):
    return "\n".join(m.value for m in at.markdown)


def test_banner_on_first_screen(start_stub, api_url):
    url = start_stub()
    api_url(url)
    at = run_app()
    assert at.warning[0].value == f"**{md(DISCLAIMER)}**"
    assert "stand-in API" in at.sidebar.info[0].value
    # the address is shown as typed (a code span shows escaping backslashes literally)
    assert any(c.value == f"`{url}`" for c in at.sidebar.caption)


def test_full_report(start_stub, api_url, good):
    api_url(start_stub(llm=lambda m: json.dumps(good)))
    at = assess(load_example(run_app()))
    assert at.text_input[0].value == "p01"
    table = at.dataframe[-1].value
    assert list(table["Drug"]) == ["metformin", "clopidogrel", "clopidogrel"]
    assert list(table["Severity"]) == ["🟥 contraindicated", "🟧 major", "🟧 major"]
    subs = [s.value for s in at.subheader]
    for s in ("Flags (3)", "Summary", "Suggestions for clinician review",
              "Each flag and its evidence"):  # fmt: skip
        assert s in subs
    text = markdown(at)
    assert "Clopidogrel must be activated by CYP2C19" in text
    assert "Consider reviewing the antiplatelet choice" in text
    assert any("Written by the AI model" in c.value for c in at.caption)
    labels = evidence_labels(at)
    assert len(labels) == 4 and labels[0].startswith("[1] DailyMed label")
    assert at.warning[0].value.startswith("**Decision support")  # banner still first
    assert at.expander[0].label == "Patient details (click to edit)"  # form folded away


def test_template_report_shows_notice(start_stub, api_url):
    api_url(start_stub())
    at = assess(load_example(run_app()))
    assert any(w.value.startswith("AI summary unavailable") for w in at.warning)
    assert any("Built from the flags alone" in c.value for c in at.caption)


def test_without_llm(start_stub, api_url):
    api_url(start_stub())
    at = assess(load_example(run_app()), use_llm=False)
    subs = [s.value for s in at.subheader]
    assert "Summary" not in subs and "Flags (3)" in subs
    assert any("AI summary turned off" in c.value for c in at.caption)
    assert len(evidence_labels(at)) == 4  # evidence still shown


def test_form_problems_listed(start_stub, api_url):
    api_url(start_stub())
    at = assess(run_app())
    msg = at.error[0].value
    assert "Add at least one prescription" in msg and "Patient ID is empty" in msg


def test_patient_not_known_to_stub(start_stub, api_url):
    api_url(start_stub())
    at = load_example(run_app())
    at.number_input[0].set_value(69)
    at = assess(at)
    assert "only answers the 10 example patients" in at.error[0].value


@pytest.mark.parametrize("url", ["http://127.0.0.1:9"])  # discard port: nothing listens
def test_api_down(api_url, url):
    api_url(url)
    at = assess(load_example(run_app()))
    assert "Cannot reach the API" in at.error[-1].value
    assert "Cannot reach the API" in at.sidebar.error[0].value


def test_weight_typed_on_screen_reaches_the_api(start_stub, api_url):
    """The stand-in answers only unchanged examples: adding a weight must change the request."""
    api_url(start_stub())
    at = load_example(run_app())
    weight = next(n for n in at.number_input if n.label.startswith("Weight"))
    assert weight.value is None
    weight.set_value(72.5)
    at = assess(at)
    assert "only answers the 10 example patients" in at.error[0].value
