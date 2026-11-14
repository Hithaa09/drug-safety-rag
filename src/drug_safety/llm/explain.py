"""The LLM layer's one entry point: flags + evidence in, a validated Report out (spec p17).

    result = explain(summary, flags, evidence, llm, finder)
    result.report, result.report_source ("llm" | "template"), result.notice, result.attempts

Loop (spec): ask, validate; on failure ask once more with the error list appended; if that
fails too, return the template report built from the flags alone, with a notice. If the model
server cannot be reached there is nothing to retry: the template is returned at once.
Every attempt is kept (answer, issues, seconds) for the evaluation and for debugging.

`explain_patient` runs the whole of Person B's part for one patient: retrieve evidence per flag,
fit it to the context budget, explain. Person A's API calls this after the rule engine.
"""

from __future__ import annotations

import time
from typing import Literal

from pydantic import BaseModel

from drug_safety.contracts import EvidenceChunk, Flag, Patient, Report
from drug_safety.llm.client import ChatFn
from drug_safety.llm.drug_names import DrugNameFinder
from drug_safety.llm.fallback import fallback_report
from drug_safety.llm.prompts import (
    PROMPT_VERSION,
    add_errors,
    build_messages,
    evidence_labels,
    patient_summary,
    resolve_labels,
    to_labels,
)
from drug_safety.llm.validator import Issue, validate

MAX_ATTEMPTS = 2  # spec: one retry
NOTICE_INVALID = (
    "AI summary unavailable: the model's answer failed the safety checks twice, so this summary "
    "was built from the flags alone."
)
NOTICE_DOWN = (
    "AI summary unavailable: the model server could not be reached, so this summary was built "
    "from the flags alone."
)


class Attempt(BaseModel):
    answer: str = ""
    issues: list[Issue] = []
    seconds: float
    error: str | None = None


class Explanation(BaseModel):
    report: Report
    report_source: Literal["llm", "template"]
    notice: str | None = None
    attempts: list[Attempt]
    prompt_version: str = PROMPT_VERSION


def explain(
    summary: str,
    flags: list[Flag],
    evidence: dict[str, list[EvidenceChunk]],
    llm: ChatFn,
    finder: DrugNameFinder,
) -> Explanation:
    messages = build_messages(summary, flags, evidence)
    labels = evidence_labels(flags, evidence)
    attempts: list[Attempt] = []
    notice = NOTICE_INVALID
    for _ in range(MAX_ATTEMPTS):
        t = time.perf_counter()
        try:
            answer = llm(messages)
        except Exception as e:  # server down, timeout, HTTP error: no point retrying
            attempts.append(Attempt(seconds=time.perf_counter() - t, error=repr(e)[:300]))
            notice = NOTICE_DOWN
            break
        seconds = time.perf_counter() - t
        report, issues = validate(resolve_labels(answer, labels), flags, evidence, summary,
                                  finder)  # fmt: skip
        attempts.append(Attempt(answer=answer, issues=issues, seconds=seconds))
        if report is not None:
            return Explanation(report=report, report_source="llm", attempts=attempts)
        messages = add_errors(messages, answer, [to_labels(i.message, labels) for i in issues])
    return Explanation(
        report=fallback_report(flags, evidence),
        report_source="template",
        notice=notice,
        attempts=attempts,
    )


def explain_patient(
    patient: Patient,
    flags: list[Flag],
    repo,
    embedder,
    llm: ChatFn,
    finder: DrugNameFinder,
    count,
    context_tokens: int,
) -> tuple[dict[str, list[EvidenceChunk]], Explanation]:
    """Retrieve -> trim to `context_tokens` (count = token counter) -> explain."""
    from drug_safety.rag.retriever import build_context, retrieve

    found = {f.flag_id: retrieve(f, repo, embedder) for f in flags}
    kept = build_context(found, context_tokens, count)
    evidence = {fid: [r.evidence for r in rs] for fid, rs in kept.items()}
    return evidence, explain(patient_summary(patient), flags, evidence, llm, finder)
