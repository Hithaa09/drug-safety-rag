"""The spec loop with a scripted fake model: accept, retry once with errors, then template."""

import json

import httpx

from drug_safety.llm.explain import NOTICE_DOWN, NOTICE_INVALID, explain

from .conftest import SUMMARY


class Scripted:
    """Returns the given answers in order and records every prompt it was sent."""

    def __init__(self, *answers):
        self.answers, self.sent = list(answers), []

    def __call__(self, messages):
        self.sent.append(messages)
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a if isinstance(a, str) else json.dumps(a)


def test_valid_first_answer_is_used(good, flags, evidence, finder):
    llm = Scripted(good)
    r = explain(SUMMARY, flags, evidence, llm, finder)
    assert r.report_source == "llm" and len(r.attempts) == 1 and r.notice is None


def test_retry_sends_the_errors_and_accepts_the_fix(good, flags, evidence, finder):
    bad = json.loads(json.dumps(good))
    bad["flags_explained"][0]["plain_explanation"] = "Prasugrel would be better."
    llm = Scripted(bad, good)
    r = explain(SUMMARY, flags, evidence, llm, finder)
    assert r.report_source == "llm" and len(r.attempts) == 2
    retry = llm.sent[1]
    assert retry[-2]["role"] == "assistant" and "Prasugrel" in retry[-2]["content"]
    assert "prasugrel" in retry[-1]["content"].lower() and "failed these checks" in retry[-1][
        "content"]  # fmt: skip


def test_two_failures_give_the_template(flags, evidence, finder):
    r = explain(SUMMARY, flags, evidence, Scripted("not json", "still not json"), finder)
    assert r.report_source == "template" and r.notice == NOTICE_INVALID
    assert len(r.attempts) == 2 and all(a.issues for a in r.attempts)
    assert {e.flag_id for e in r.report.flags_explained} == {f.flag_id for f in flags}


def test_server_down_gives_the_template_without_retry(flags, evidence, finder):
    llm = Scripted(httpx.ConnectError("refused"))
    r = explain(SUMMARY, flags, evidence, llm, finder)
    assert r.report_source == "template" and r.notice == NOTICE_DOWN
    assert len(r.attempts) == 1 and "ConnectError" in r.attempts[0].error


def test_answer_in_labels_is_resolved_and_retry_speaks_labels(good, flags, evidence, finder):
    from drug_safety.llm.prompts import evidence_labels

    inv = evidence_labels(flags, evidence)
    labelled = json.loads(json.dumps(good))
    for item in labelled["flags_explained"] + labelled["suggestions_for_clinician_review"]:
        item["evidence_ids"] = [inv[x] for x in item["evidence_ids"]]
    bad = json.loads(json.dumps(labelled))
    bad["flags_explained"][0]["evidence_ids"] = ["E3"]  # E3 belongs to ddi-001
    llm = Scripted(bad, labelled)
    r = explain(SUMMARY, flags, evidence, llm, finder)
    assert r.report_source == "llm" and len(r.attempts) == 2
    assert r.report.flags_explained[0].evidence_ids == ["cpic-clopidogrel-01"]  # real IDs
    retry = llm.sent[1][-1]["content"]
    assert "'label-clop-ddi'" not in retry and "E3" in retry and "E1" in retry
