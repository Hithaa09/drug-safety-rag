"""Regression test on real Qwen answers, second Colab run (2026-11-14, prompt explain-v2,
guided JSON and plain; docs/llm_manual_review.md, "Run 2").

Each attempt has the set of validator rules it must break (empty = accepted). Answers cite
evidence by label (E1, ...), so they are resolved the way explain.py does before validation.
"""

import json
from pathlib import Path

import pytest

from drug_safety.contracts import EvidenceChunk, Flag
from drug_safety.llm.prompts import evidence_labels, resolve_labels
from drug_safety.llm.validator import validate

FIXTURE = Path(__file__).parent / "fixtures" / "qwen_2026-11-14_explain-v2.json"
CASES = {c["case"]: c for c in json.loads(FIXTURE.read_text())["cases"]}

S6, OK = {6}, set()
# case -> expected rules broken, per attempt. Notes on the ones this run's validator let through
# and today's validator rejects:
EXPECTED = {
    "guided/p01-worked-example": [S6, OK],
    # "she requires a lower dose of warfarin": a dose change stated as needed.
    "guided/p02-warfarin": [S6],
    "guided/p03-codeine-paracetamol": [S6, OK],
    "guided/p04-simvastatin": [OK],
    "guided/p05-methotrexate": [S6, OK],
    "guided/p06-clozapine": [S6, OK],
    "guided/p07-atorvastatin": [S6, OK],
    "guided/p08-clopidogrel-ssri": [OK],
    # 2nd: "necessitates a lower starting dose of allopurinol".
    "guided/p09-tramadol-theophylline": [S6, S6],
    # 2nd: "Consider reviewing the dosing of phenytoin ... and adjusting it".
    "guided/p10-cyp2c9-cyp2c19": [S6, S6],
    # 2nd: "adjusting clopidogrel if necessary", "considering alternative glucose-lowering
    # therapies".
    "plain/p01-worked-example": [S6, S6],
    "plain/p02-warfarin": [S6],
    "plain/p03-codeine-paracetamol": [S6, OK],
    "plain/p04-simvastatin": [OK],
    "plain/p05-methotrexate": [S6, OK],
    "plain/p06-clozapine": [S6, S6],
    "plain/p07-atorvastatin": [S6, OK],
    "plain/p08-clopidogrel-ssri": [OK],
    "plain/p09-tramadol-theophylline": [S6, S6],
    # 2nd: a missing comma, not valid JSON (guided JSON prevents this).
    "plain/p10-cyp2c9-cyp2c19": [S6, {1}],
}


def _case(name):
    c = CASES[name]
    flags = [Flag.model_validate(f) for f in c["flags"]]
    ev = {k: [EvidenceChunk.model_validate(x) for x in v] for k, v in c["evidence"].items()}
    return c, flags, ev, evidence_labels(flags, ev)


def test_fixture_covers_every_case():
    assert set(CASES) == set(EXPECTED)


@pytest.mark.parametrize("name", EXPECTED)
def test_real_answer_verdicts(name, finder):
    c, flags, ev, labels = _case(name)
    assert len(c["answers"]) == len(EXPECTED[name])
    for n, (answer, want) in enumerate(zip(c["answers"], EXPECTED[name], strict=True), 1):
        report, issues = validate(resolve_labels(answer, labels), flags, ev, c["summary"],
                                  finder, require_quotes=False)  # fmt: skip
        got = {i.rule for i in issues}
        assert got == want, f"attempt {n}: {[i.message for i in issues]}"
        assert (report is not None) == (not want)


def test_the_new_messages_name_the_problem(finder):
    for name, attempt, phrase in [
        ("guided/p09-tramadol-theophylline", 1, "necessitates a lower starting dose"),
        ("guided/p10-cyp2c9-cyp2c19", 1, '("adjusting")'),
        ("plain/p01-worked-example", 1, "considering alternative"),
        ("guided/p02-warfarin", 0, "requires a lower dose"),
    ]:
        c, flags, ev, labels = _case(name)
        _, issues = validate(resolve_labels(c["answers"][attempt], labels), flags, ev,
                             c["summary"], finder, require_quotes=False)  # fmt: skip
        assert phrase in " ".join(i.message for i in issues), name
