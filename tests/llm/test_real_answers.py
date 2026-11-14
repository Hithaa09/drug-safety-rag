"""Regression test on real Qwen answers (first Colab run, 2026-11-11, prompt explain-v1).

Each answer has the verdict a careful human reviewer gives it (README, "First real run"):
the set of validator rules it must break, empty = it must be accepted. These keep the
validator honest in both directions on real model output, not only on hand-made examples.
"""

import json
from pathlib import Path

import pytest

from drug_safety.contracts import EvidenceChunk, Flag
from drug_safety.llm.validator import validate

FIXTURE = Path(__file__).parent / "fixtures" / "qwen_2026-11-11_plain.json"
CASES = {c["case"]: c for c in json.loads(FIXTURE.read_text())["cases"]}

# case -> expected rules broken, per attempt. Why, for the ones that are not obvious:
EXPECTED = {
    # 1st: "Consider discontinuing or adjusting the dose of metformin".
    # 2nd: "Consider alternative antiplatelet therapy ..." is a switch, not a review.
    "p01-worked-example": [{6}, {6}],
    # 1st: life-004 left out. ("may require a lower dose" describes the label: allowed.)
    "p02-warfarin": [{2}, set()],
    "p03-codeine-paracetamol": [{2}, set()],
    "p04-simvastatin": [set()],
    # 2nd: "consider reducing the dosage or discontinuing methotrexate", "potentially
    # adjusting the methotrexate dosage".
    "p05-methotrexate": [{2}, {6}],
    # "Consider reviewing ... potential dosage adjustments" is a review: allowed.
    "p06-clozapine": [{2}, set()],
    "p07-atorvastatin": [set()],
    "p08-clopidogrel-ssri": [set()],
    # "Dose adjustments are necessary", "a need for dose adjustment", "requires ...
    # consideration of dose adjustments": accepted by explain-v1's validator, wrongly.
    "p09-tramadol-theophylline": [{6}],
    "p10-cyp2c9-cyp2c19": [set()],
}


def _case(name):
    c = CASES[name]
    flags = [Flag.model_validate(f) for f in c["flags"]]
    ev = {k: [EvidenceChunk.model_validate(x) for x in v] for k, v in c["evidence"].items()}
    return c, flags, ev


@pytest.mark.parametrize("name", EXPECTED)
def test_real_answer_verdicts(name, finder):
    c, flags, ev = _case(name)
    assert len(c["answers"]) == len(EXPECTED[name])
    for n, (answer, want) in enumerate(zip(c["answers"], EXPECTED[name], strict=True), 1):
        report, issues = validate(answer, flags, ev, c["summary"], finder, require_quotes=False)
        got = {i.rule for i in issues}
        assert got == want, f"attempt {n}: {[i.message for i in issues]}"
        assert (report is not None) == (not want)


def test_the_messages_name_the_problem(finder):
    c, flags, ev = _case("p09-tramadol-theophylline")
    msgs = " ".join(i.message for i in validate(c["answers"][0], flags, ev, c["summary"],
                                                finder, require_quotes=False)[1])  # fmt: skip
    assert "Dose adjustments are necessary" in msgs and "need for dose adjustment" in msgs
    c, flags, ev = _case("p01-worked-example")
    msgs = " ".join(i.message for i in validate(c["answers"][1], flags, ev, c["summary"],
                                                finder, require_quotes=False)[1])  # fmt: skip
    assert "Consider alternative" in msgs
