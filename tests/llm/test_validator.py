"""Validator: the spec's own example passes, a correct answer passes, and every rule fails
when it should. Each `bad` case breaks exactly one thing in the good answer."""

import copy
import json
from pathlib import Path

import pytest

from drug_safety.llm.fallback import fallback_report
from drug_safety.llm.validator import instruction_problems, numbers, validate

from .conftest import SUMMARY


def run(answer, flags, evidence, finder, summary=SUMMARY, require_quotes=True):
    text = answer if isinstance(answer, str) else json.dumps(answer)
    return validate(text, flags, evidence, summary, finder, require_quotes)


def rules(issues):
    return sorted({i.rule for i in issues})


def test_good_answer_passes(good, flags, evidence, finder):
    report, issues = run(good, flags, evidence, finder)
    assert issues == [] and report is not None


def test_spec_p19_example_passes_for_its_flag(flags, evidence, finder):
    p19 = Path("tests/contracts/spec_examples/report_p19.json").read_text()
    # the spec's example has no quotes (explain-v3 adds them, checked, then dropped)
    report, issues = run(p19, flags[:1], {"gene-001": evidence["gene-001"]}, finder,
                         require_quotes=False)  # fmt: skip
    assert issues == [], issues


def test_markdown_fence_tolerated(good, flags, evidence, finder):
    assert run("```json\n" + json.dumps(good) + "\n```", flags, evidence, finder)[1] == []


def edit(good, path, value):
    out = copy.deepcopy(good)
    *keys, last = path
    node = out
    for k in keys:
        node = node[k]
    if value is DELETE:
        del node[last]
    else:
        node[last] = value
    return out


DELETE = object()
E, S = "flags_explained", "suggestions_for_clinician_review"
BAD = {
    "not JSON": (1, None),
    "extra field": (1, (["extra"], "x")),
    "missing summary": (1, (["summary"], DELETE)),
    "unknown flag_id": (2, ([E, 0, "flag_id"], "gene-999")),
    "flag not explained": (2, ([E], "DROP_LAST")),
    "evidence from another flag": (3, ([E, 0, "evidence_ids"], ["label-met-renal"])),
    "evidence never supplied": (3, ([E, 1, "evidence_ids"], ["chunk-123"])),
    "explanation without evidence": (3, ([E, 2, "evidence_ids"], [])),
    "drug outside prescriptions and flags": (
        4,
        (
            [E, 0, "plain_explanation"],
            "Clopidogrel works poorly here; prasugrel does not need CYP2C19.",
        ),
    ),  # fmt: skip
    "suggestion cites nothing": (5, ([S, 0, "evidence_ids"], [])),
    "order to stop": (6, ([E, 1, "plain_explanation"], "Stop omeprazole now.")),
    "order after a colon": (6, ([E, 1, "plain_explanation"], "Note: Stop omeprazole.")),
    "should be discontinued": (
        6,
        ([E, 1, "plain_explanation"], "Omeprazole should be discontinued."),
    ),
    "dose should change": (
        6,
        ([E, 2, "plain_explanation"], "The metformin dose should be reduced."),
    ),
    "suggestion to reduce dose": (6, ([S, 1, "text"], "Consider reducing the metformin dose.")),
    "suggestion to switch": (
        6,
        ([S, 0, "text"], "Consider reviewing a switch to another antiplatelet."),
    ),
    "suggestion not 'Consider'": (6, ([S, 0, "text"], "Review the antiplatelet choice.")),
    "invented number": (7, ([E, 2, "plain_explanation"], "Metformin 850 mg is too much here.")),
}


@pytest.mark.parametrize("name", BAD)
def test_each_rule_rejects(name, good, flags, evidence, finder):
    rule, change = BAD[name]
    if change is None:
        answer = "Here is the report: {summary: ...}"
    elif change[1] == "DROP_LAST":
        answer = copy.deepcopy(good)
        answer[E] = answer[E][:-1]
    else:
        answer = edit(good, *change)
    report, issues = run(answer, flags, evidence, finder)
    assert report is None
    assert rules(issues) == [rule], [i.message for i in issues]


def test_duplicate_explanation_rejected(good, flags, evidence, finder):
    good[E].append(dict(good[E][0]))
    assert rules(run(good, flags, evidence, finder)[1]) == [2]


def test_flag_without_evidence_must_be_named_in_uncertainties(good, flags, evidence, finder):
    evidence["lab-001"] = []
    good[E][2]["evidence_ids"] = []
    good[E][2]["quotes"] = []  # nothing to quote
    good[S] = good[S][:1]
    good[E][2]["plain_explanation"] = "The eGFR of 28 is below the limit of 30."
    assert rules(run(good, flags, evidence, finder)[1]) == [3]
    good["uncertainties"] = ["No evidence passage was found for lab-001 (metformin)."]
    assert run(good, flags, evidence, finder)[1] == []


def test_brand_of_a_flag_drug_and_prescribed_brand_allowed(good, flags, evidence, finder):
    good[E][0]["plain_explanation"] = "Plavix (clopidogrel) needs CYP2C19 to work."
    assert run(good, flags, evidence, finder)[1] == []
    good[E][1]["plain_explanation"] = "Nexium is also taken; it acts like omeprazole."
    assert rules(run(good, flags, evidence, finder)[1]) == [4]  # not prescribed: rejected
    assert run(good, flags, evidence, finder, SUMMARY + "\nAlso: Nexium 20 mg.")[1] == []


def test_numbers_from_evidence_and_flags_allowed(good, flags, evidence, finder):
    good[E][1]["plain_explanation"] = "Omeprazole cut the active metabolite by 45%."
    good[E][2]["plain_explanation"] = "eGFR 28 mL/min/1.73 m2 is under the 30 limit."
    assert run(good, flags, evidence, finder)[1] == []
    assert numbers("1,000/µL, CYP2C19, *2/*2, 1.73 m2, 28.0") == {1000.0, 2.0, 1.73, 28.0}


@pytest.mark.parametrize(
    "text",
    [
        "Lower kidney function means metformin can build up.",
        "Reduced CYP2C19 activity lowers clopidogrel's effect.",
        "Smoking cessation can raise clozapine levels.",
        "Patients should be monitored for withdrawal symptoms.",
        "The label advises a reduced starting dose in kidney impairment.",
        "The guideline suggests an alternative antiplatelet.",
        "CYP2C9 poor metabolizer: lower warfarin dose requirement.",  # an engine reason
    ],
)
def test_descriptions_are_not_instructions(text):
    assert instruction_problems(text, suggestion=False) == []


@pytest.mark.parametrize(
    "text",
    [
        "Consider reviewing the antiplatelet choice.",
        "Consider reviewing metformin given reduced kidney function.",
        "Consider reviewing the dose given reduced kidney function.",
        "Consider reviewing clozapine levels if smoking cessation occurs.",
    ],
)
def test_review_suggestions_pass(text):
    assert instruction_problems(text, suggestion=True) == []


@pytest.mark.parametrize(
    "text, suggestion",
    [  # second Colab run: accepted by that run's validator, wrongly
        ("Reduced kidney function necessitates a lower starting dose of allopurinol.", False),
        ("The patient requires a lower dose of warfarin.", False),
        ("Consider reviewing the dosing of phenytoin and adjusting it as needed.", True),
        ("Consider monitoring the response and adjusting clopidogrel if necessary.", True),
        (
            "Consider checking the eGFR and considering alternative glucose-lowering therapies.",
            True,
        ),
    ],
)
def test_second_run_instructions_rejected(text, suggestion):
    assert instruction_problems(text, suggestion=suggestion)


@pytest.mark.parametrize(
    "text, suggestion",
    [
        ("The label says patients may require a lower dose.", False),
        ("Smoking is lowering clozapine levels.", False),
        ("Consider reviewing alternative options described in the guideline.", True),
        ("Consider monitoring theophylline levels to ensure appropriate dosing.", True),
        ("Consider monitoring for myopathy, as increasing exposure raises the risk.", True),
    ],
)
def test_second_run_rules_keep_descriptions_and_reviews(text, suggestion):
    assert instruction_problems(text, suggestion=suggestion) == []


def test_template_fallback_passes_the_validator(flags, evidence, finder):
    report = fallback_report(flags, evidence)
    # the template is not model output: it quotes nothing
    assert run(report.model_dump_json(), flags, evidence, finder, require_quotes=False)[1] == []
    evidence["lab-001"] = []
    report = fallback_report(flags, evidence)
    assert run(report.model_dump_json(), flags, evidence, finder, require_quotes=False)[1] == []
    assert any("lab-001" in u for u in report.uncertainties)


# ---------------------------------------------------------------- rule 8: quotes (explain-v3)


def test_quotes_must_be_word_for_word_from_that_flags_evidence(good, flags, evidence, finder):
    q = good[E][1]["quotes"][0]
    # whitespace, a line break and curly quotes don't matter
    q["text"] = "Omeprazole reduced active\nmetabolite   exposure by 45%."
    assert run(good, flags, evidence, finder)[1] == []
    for bad, why in [
        ("Omeprazole strongly reduced active metabolite exposure by 45%.", "not word for word"),
        ("Omeprazole reduced exposure.", "words"),  # too short to prove anything
    ]:
        q["text"] = bad
        issues = run(good, flags, evidence, finder)[1]
        assert rules(issues) == [8] and why in issues[0].message, bad
    q.update(
        evidence_id="cpic-clopidogrel-01",
        text="CYP2C19 poor metabolizers form less "
        "active metabolite; consider an alternative antiplatelet.",
    )  # gene-001's passage
    assert "not supplied for ddi-001" in run(good, flags, evidence, finder)[1][0].message


def test_explanation_with_evidence_needs_a_quote(good, flags, evidence, finder):
    good[E][0]["quotes"] = []
    issues = run(good, flags, evidence, finder)[1]
    assert rules(issues) == [8] and "has no quotes" in issues[0].message
    good[E][0]["quotes"] = [good[E][1]["quotes"][0]] * 4
    assert any("at most 3" in i.message for i in run(good, flags, evidence, finder)[1])


def test_quotes_may_name_alternatives_and_doses_and_are_dropped(good, flags, evidence, finder):
    """A verbatim copy of the evidence is exempt from rules 4, 6, 7 (names prasugrel, says
    avoid): the quote is the allowed place for the evidence's own instructions."""
    evidence["ddi-001"][0] = evidence["ddi-001"][0].model_copy(
        update={
            "text": (
                "Avoid concomitant use of Plavix with omeprazole. Use prasugrel 10 mg instead."
            )
        }
    )
    good[E][1]["quotes"] = [{"evidence_id": "label-clop-ddi", "text": "Avoid concomitant use "
                             "of Plavix with omeprazole."},
                            {"evidence_id": "label-clop-ddi", "text": "Use prasugrel 10 mg "
                             "instead."}]  # fmt: skip
    good[E][1]["plain_explanation"] = "The label advises against taking omeprazole with it."
    report, issues = run(good, flags, evidence, finder)
    assert issues == [], issues  # "Avoid", "prasugrel", "10" are fine inside a quote
    assert not hasattr(report.flags_explained[1], "quotes")  # the Report contract is unchanged
    good[E][1]["plain_explanation"] = "Use prasugrel 10 mg instead."  # same words, own voice
    assert rules(run(good, flags, evidence, finder)[1]) == [4]  # 10 is in the evidence: ok
