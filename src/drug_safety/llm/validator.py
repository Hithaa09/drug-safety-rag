"""Accept or reject one LLM answer (spec p16 validator, rules 1-6, plus rule 7 below).

    report, issues = validate(text, flags, evidence, patient_summary, finder)
    if not issues: use report
    else: retry once with [i.message for i in issues] appended (prompts.add_errors)

The rules, as the spec states them:
  1. It parses into the Report model.
  2. Every flag_id exists in the flags passed in, and every flag has (exactly one) explanation.
  3. Every evidence_id was among the chunks supplied for that flag. Explanations of a flag
     that has evidence cite at least one; a flag with no evidence is named under
     `uncertainties` (spec prompt rules).
  4. No drug name appears that is missing from the patient's prescriptions and the flags.
  5. Every suggestion cites at least one evidence ID.
  6. No instruction to stop a drug or change a dose, anywhere: orders ("Stop X"), modal
     instructions ("should be discontinued"), and a dose change stated as needed ("dose
     adjustments are necessary", "consider reducing the dose"). Suggestions must start
     "Consider reviewing" (or monitoring, checking, assessing, evaluating, discussing) and may
     not suggest stopping, switching or changing a dose. Describing the evidence ("the label
     recommends a lower starting dose", "may require a lower dose") is allowed.
Added by Person B (not in the spec's list, in its prompt rules):
  7. No number appears that is not in the flags, the evidence or the patient summary
     ("never add a dose or number"). Recorded separately so its effect can be measured.
  8. (explain-v3) Every explanation of a flag that has evidence carries 1-3 `quotes`, each copied
     word for word from one of that flag's passages. The quotes are checked here and then
     dropped: the Report contract (spec) is unchanged. Why: run 2's wrong statements (smoking
     "increases adverse reactions", "both drugs interfere with serotonin reuptake") came from
     the model paraphrasing with nothing tying each claim to a passage; and the evidence's own
     instructions ("avoid", "reduce the dose"), which the prompt forbade restating, had no
     allowed place to go. A quote is that place, and it can be checked mechanically. Rules 4,
     6, 7 don't apply to quotes (an exact copy of the evidence may name alternatives or doses).

Messages are written for Qwen to read in the retry, and for a person reading the eval output.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from drug_safety.contracts import EvidenceChunk, Flag, FlagExplanation, Report
from drug_safety.llm.drug_names import DrugNameFinder


class Issue(BaseModel):
    rule: int
    where: str
    message: str


# ---------------------------------------------------------------- the model's answer (v3)


class Quote(BaseModel):
    model_config = {"extra": "forbid"}
    evidence_id: str
    text: str


class AnswerExplanation(FlagExplanation):
    quotes: list[Quote]  # required in the guided schema, so the model can't leave it out


class Answer(Report):
    """What Qwen returns from explain-v3: the Report plus, per explanation, verbatim quotes.
    The schema sent to vLLM for guided JSON; the quotes are validated, then dropped."""

    flags_explained: list[AnswerExplanation]


QUOTE_WORDS = (5, 80)  # a quote shorter than this proves little; longer is a copy of the passage
MAX_QUOTES = 3


def _norm(text: str) -> str:
    """Whitespace, quote marks and list bullets normalised: a quote copied across a line break
    or with straight quotes still matches."""
    text = text.replace("\u2019", "'").replace("\u2018", "'").replace("\u201c", '"')
    text = text.replace("\u201d", '"').replace("\u2022", " ").replace("- •", " ")
    return " ".join(text.split()).lower()


# ---------------------------------------------------------------- rule 1: parse


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def parse(text: str) -> tuple[dict | None, list[Issue]]:
    body = text.strip()
    if m := _FENCE.match(body):  # a markdown fence around valid JSON is tolerated
        body = m.group(1)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        return None, [Issue(rule=1, where="answer", message=f"not valid JSON ({e.msg}); "
                            "return only the JSON object")]  # fmt: skip
    if not isinstance(data, dict):
        return None, [Issue(rule=1, where="answer", message="the answer must be one JSON object")]
    return data, []


def take_quotes(data: dict) -> dict[str, list]:
    """Remove the v3 `quotes` from each explanation (the Report has no such field); return them
    by flag_id, as given (checked in validate)."""
    out: dict[str, list] = {}
    for e in data.get("flags_explained") or []:
        if isinstance(e, dict) and "quotes" in e:
            out.setdefault(str(e.get("flag_id")), []).extend(e.pop("quotes") or [])
    return out


def to_report(data: dict) -> tuple[Report | None, list[Issue]]:
    issues = []
    for i, s in enumerate(data.get("suggestions_for_clinician_review") or []):
        if isinstance(s, dict) and not s.get("evidence_ids"):  # rule 5 before the schema
            issues.append(Issue(rule=5, where=f"suggestion {i + 1}",
                                message=f"suggestion {i + 1} cites no evidence ID; cite one, or "
                                "move the point to uncertainties"))  # fmt: skip
    if issues:
        return None, issues
    try:
        return Report.model_validate(data), []
    except ValidationError as e:
        return None, [
            Issue(rule=1, where=".".join(str(x) for x in err["loc"]) or "answer",
                  message=f"schema: {'.'.join(str(x) for x in err['loc'])}: {err['msg']}")
            for err in e.errors()
        ]  # fmt: skip


# ---------------------------------------------------------------- rule 6: instructions

_STOP_BASE = r"stop|discontinue|cease|withhold|withdraw|switch|replace|substitute|swap|avoid|quit"
_STOP_ANY = (
    r"\b(?:stop(?:s|ped|ping)?|discontinu\w*|ceas(?:e|es|ed|ing)|withh(?:old|olds|eld|olding)|"
    r"withdraw(?:s|n|ing)?|switch\w*|replac\w*|substitut\w*|swap\w*|avoid\w*|quit\w*|halt\w*)\b"
)
_DOSE_VERB = r"(?:increas|decreas|reduc|lower|rais|halv|doubl|adjust|titrat|chang|cut|modif|tap)\w*"
_DOSE_NOUN = r"(?:dose|doses|dosage|dosing)"
_MODAL = r"\b(?:should|must|needs? to|has to|have to|ought to|is advised to|are advised to)\s+"

# Direct instructions, wherever they appear. Dose verbs at the start of a sentence count only
# when the sentence is about a dose ("Lower kidney function ..." is not an order).
_IMPERATIVE = re.compile(
    rf"^(?:please\s+)?(?:{_STOP_BASE}|do not (?:take|use|give|prescribe|start)|"
    r"don't (?:take|use|give)|never (?:take|use|give))\b",
    re.I,
)
_IMPERATIVE_DOSE = re.compile(
    r"^(?:please\s+)?(?:increase|decrease|reduce|lower|raise|halve|double|adjust|titrate|change|"
    r"cut)\b",
    re.I,
)
_MODAL_STOP = re.compile(rf"{_MODAL}(?:\w+\s+){{0,3}}?{_STOP_ANY}", re.I)
_MODAL_NOT_TAKE = re.compile(
    rf"{_MODAL}not\s+(?:be\s+)?(?:take|taken|use|used|give|given|prescribed|co-?administered)\b",
    re.I,
)
_MODAL_DOSE = re.compile(rf"{_MODAL}(?:\w+\s+){{0,3}}?{_DOSE_VERB}", re.I)
_DOSE_STEM = r"(?:adjust|reduc|chang|modif|lower|decreas|increas|titrat)\w*"
_W = r"(?:[\w'*/]+,?\s+)"  # one word (and its space) inside a phrase
# A dose change stated as needed, in any field (found in real Qwen answers, first Colab run).
_DOSE_NEEDED = [
    # "Dose adjustments are necessary"
    re.compile(rf"\b{_DOSE_NOUN}\s+(?:adjustments?|reductions?|changes?|modifications?)\s+"
               r"(?:is|are|will be|would be)\s+(?:necessary|required|needed|warranted|essential)\b",
               re.I),
    # "a need for dose adjustment"
    re.compile(rf"\bneed\s+for\s+(?:a\s+)?{_DOSE_NOUN}\s+{_DOSE_STEM}", re.I),
    # "requires careful monitoring and consideration of dose adjustments" (not after "may")
    re.compile(rf"(?<!may )(?<!might )(?<!could )(?<!can )\b(?:requires?|requiring|"
               rf"necessitat\w*|warrants?|warranting|calls? for)\s+{_W}{{0,5}}?{_DOSE_NOUN}\s+"
               rf"{_DOSE_STEM}", re.I),
    # "consideration of dose adjustments", "consider reducing the dosage"
    re.compile(rf"\bconsider(?:ing|ation of)?\s+{_W}{{0,3}}?{_DOSE_NOUN}\s+{_DOSE_STEM}", re.I),
    re.compile(rf"\bconsider(?:ing|ation of)?\s+{_W}{{0,2}}?{_DOSE_STEM}\s+{_W}{{0,2}}?"
               rf"{_DOSE_NOUN}\b", re.I),
    # "necessitates a lower starting dose", "requires a lower dose" (second Colab run).
    # Not after "may": "may require a lower dose" describes the label.
    re.compile(rf"(?<!may )(?<!might )(?<!could )(?<!can )\b(?:requires?|requiring|"
               rf"necessitat\w*|warrants?|warranting|calls? for|needs?)\s+{_W}{{0,2}}?"
               rf"(?:lower|reduced|higher|increased|smaller|larger|adjusted|modified)\s+"
               rf"{_W}{{0,2}}?{_DOSE_NOUN}\b", re.I),
]  # fmt: skip
# "consider withholding, dose reducing, or discontinuing methotrexate"
_CONSIDER_STOP = re.compile(rf"\bconsider(?:ing|ation of)?\s+{_W}{{0,3}}?{_STOP_ANY}", re.I)
# In a suggestion, a dose change as the action: "adjusting the dosage", "reduce the dose".
# ("Consider reviewing ... potential dosage adjustments" names what to review: allowed.)
_DOSE_ACTION = re.compile(
    r"\b(?:reduc|lower|decreas|increas|adjust|chang|modify|halv|doubl|titrat)ing\s+"
    rf"{_W}{{0,3}}?{_DOSE_NOUN}\b|\b(?:reduce|lower|decrease|increase|adjust|change|modify|halve|"
    rf"double|titrate)\s+(?:the|its|their|his|her)\s+{_W}{{0,2}}?{_DOSE_NOUN}\b",
    re.I,
)
# In a suggestion, a dose change with the drug or "it" as object: "adjusting it according to
# drug monitoring", "adjusting clopidogrel if necessary" (second Colab run). "Adjusting" and
# "titrating" are always about the dose; the other verbs only with "it"/"them".
_DOSE_OBJECT_ACTION = re.compile(
    r"\b(?:adjust|titrat)ing\b|\b(?:adjust|titrate)\s+(?:it|them)\b|"
    r"\b(?:reduc|lower|decreas|increas|halv|doubl|modify)ing\s+(?:it|them)\b",
    re.I,
)
# In a suggestion, a switch phrased as considering an alternative: "considering alternative
# glucose-lowering therapies". ("Consider reviewing alternative ..." is a review: allowed.)
_CONSIDER_ALTERNATIVE = re.compile(
    r"\bconsider(?:ing|ation of)?\s+(?:the\s+use\s+of\s+)?(?:an?\s+)?"
    r"(?:alternative|alternate|another|different)\b",
    re.I,
)
REVIEW_VERBS = ("reviewing", "monitoring", "checking", "assessing", "evaluating", "discussing")
_SUGGESTION_START = re.compile(rf"\s*consider\s+(?:{'|'.join(REVIEW_VERBS)})\b", re.I)
# Split at sentence ends, and at a colon only before a capital: "Note: Stop it." is two
# sentences, but an engine reason like "poor metabolizer: lower dose requirement" is one.
_SENTENCES = re.compile(r"(?<=[.!?;])\s+|(?<=:)\s+(?=[A-Z])|\n+")


def instruction_problems(text: str, suggestion: bool) -> list[str]:
    """Why this text reads as an instruction to stop a drug or change a dose (empty = fine)."""
    out = []
    if suggestion and not _SUGGESTION_START.match(text):
        start = " ".join(text.split()[:3])
        out.append(f'must start with "Consider reviewing" (or "Consider monitoring", "Consider '
                   f'checking"), not "{start}"')  # fmt: skip
    for s in (x.strip() for x in _SENTENCES.split(text)):
        if not s:
            continue
        about_dose = re.search(rf"\b{_DOSE_NOUN}\b", s, re.I)
        needed = next((m for p in _DOSE_NEEDED if (m := p.search(s))), None)
        if _IMPERATIVE.match(s) or (_IMPERATIVE_DOSE.match(s) and about_dose):
            out.append(f'gives an order ("{s[:80]}")')
        elif _MODAL_STOP.search(s) or _MODAL_NOT_TAKE.search(s):
            out.append(f'says a drug should be stopped or avoided ("{s[:80]}")')
        elif _MODAL_DOSE.search(s) and about_dose:
            out.append(f'says a dose should change ("{s[:80]}")')
        elif needed:
            out.append(f'says a dose change is needed ("{needed.group()}")')
        elif m := _CONSIDER_STOP.search(s):
            out.append(f'suggests stopping or switching ("{m.group()}")')
        elif suggestion and (m := re.search(_STOP_ANY, s, re.I)):
            out.append(f'suggests stopping or switching ("{m.group()}")')
        elif suggestion and (m := _DOSE_ACTION.search(s) or _DOSE_OBJECT_ACTION.search(s)):
            out.append(f'suggests a dose change ("{m.group()}")')
        elif suggestion and (m := _CONSIDER_ALTERNATIVE.search(s)):
            out.append(f'suggests switching to another drug ("{m.group()}")')
    return out


# ---------------------------------------------------------------- rule 7: numbers

_NUMBER = re.compile(r"(?<![A-Za-z\d.])\d+(?:\.\d+)?(?!\d)")


def numbers(text: str) -> set[float]:
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)  # 1,000 -> 1000
    return {float(n) for n in _NUMBER.findall(text)}


# ---------------------------------------------------------------- all rules


def texts(report: Report) -> list[tuple[str, str, bool]]:
    """(where, text, is_suggestion) for every free-text field."""
    out = [("summary", report.summary, False)]
    out += [(f"explanation of {e.flag_id}", e.plain_explanation, False)
            for e in report.flags_explained]  # fmt: skip
    out += [(f"suggestion {i + 1}", s.text, True)
            for i, s in enumerate(report.suggestions_for_clinician_review)]  # fmt: skip
    out += [(f"uncertainty {i + 1}", u, False) for i, u in enumerate(report.uncertainties)]
    return out


def allowed_drugs(flags: list[Flag], patient_summary: str, finder: DrugNameFinder) -> set[str]:
    """Flag drugs plus every drug named in the patient summary (its prescription list)."""
    out = finder.find(patient_summary)
    for f in flags:
        out |= {f.drug, *([f.interacting_drug] if f.interacting_drug else [])}
        out |= finder.find(f.reason)
    return out


def source_numbers(
    flags: list[Flag], evidence: dict[str, list[EvidenceChunk]], patient_summary: str
) -> set[float]:
    src = [patient_summary, *(f.model_dump_json() for f in flags)]
    src += [f"{c.title or ''} {c.heading or ''} {c.text}" for cs in evidence.values() for c in cs]
    return numbers(" ".join(src)) | {float(len(flags))}


def validate(
    text: str,
    flags: list[Flag],
    evidence: dict[str, list[EvidenceChunk]],
    patient_summary: str,
    finder: DrugNameFinder,
    require_quotes: bool = True,
) -> tuple[Report | None, list[Issue]]:
    """require_quotes=False judges answers from prompts before explain-v3 (runs 1 and 2)."""
    data, issues = parse(text)
    if issues:
        return None, issues
    quotes = take_quotes(data)
    report, issues = to_report(data)
    if issues:
        return None, issues

    by_id = {f.flag_id: f for f in flags}
    supplied = {f.flag_id: {c.chunk_id for c in evidence.get(f.flag_id, [])} for f in flags}

    def add(rule, where, message):
        issues.append(Issue(rule=rule, where=where, message=message))

    # rule 2
    explained = [e.flag_id for e in report.flags_explained]
    for fid in dict.fromkeys(explained):
        if fid not in by_id:
            add(2, f"explanation of {fid}", f"flag_id {fid!r} is not one of the flags given")
        elif explained.count(fid) > 1:
            add(2, f"explanation of {fid}", f"{fid} is explained more than once; give one entry")
    for fid in by_id:
        if fid not in explained:
            add(2, "flags_explained", f'flag {fid} has no explanation: add an entry with '
                f'"flag_id": "{fid}" to flags_explained (one per flag)')  # fmt: skip
    for i, s in enumerate(report.suggestions_for_clinician_review):
        if s.flag_id not in by_id:
            add(2, f"suggestion {i + 1}", f"suggestion {i + 1} names unknown flag_id {s.flag_id!r}")

    # rule 3 (and 5's "cites at least one", already enforced for suggestions by to_report)
    cited = [(f"explanation of {e.flag_id}", e.flag_id, e.evidence_ids)
             for e in report.flags_explained]  # fmt: skip
    cited += [(f"suggestion {i + 1}", s.flag_id, s.evidence_ids)
              for i, s in enumerate(report.suggestions_for_clinician_review)]  # fmt: skip
    for where, fid, ids in cited:
        if fid not in by_id:
            continue
        for eid in ids:
            if eid not in supplied[fid]:
                ok = sorted(supplied[fid]) or "none"
                add(3, where, f"evidence ID {eid!r} was not supplied for {fid} (allowed: {ok})")
    for e in report.flags_explained:
        if e.flag_id in by_id and supplied[e.flag_id] and not e.evidence_ids:
            add(3, f"explanation of {e.flag_id}", f"explanation of {e.flag_id} cites no evidence")
    unc = " ".join(report.uncertainties).lower()
    for fid, ids in supplied.items():
        if not ids and fid not in unc and by_id[fid].drug.lower() not in unc:
            add(3, "uncertainties", f"flag {fid} has no evidence; say so under uncertainties")

    # rule 8: quotes, word for word from that flag's own passages
    if require_quotes:
        text_of = {c.chunk_id: _norm(c.text) for cs in evidence.values() for c in cs}
        for e in report.flags_explained:
            fid, where = e.flag_id, f"explanation of {e.flag_id}"
            if fid not in by_id:
                continue
            qs = quotes.get(fid, [])
            if supplied[fid] and not qs:
                add(8, where, f'{where} has no quotes: copy one to three sentences that support '
                    'it, word for word, from its evidence into "quotes"')  # fmt: skip
            if len(qs) > MAX_QUOTES:
                add(8, where, f"{where} has {len(qs)} quotes; give at most {MAX_QUOTES}")
            for n, q in enumerate(qs, 1):
                try:
                    q = Quote.model_validate(q)
                except ValidationError:
                    add(8, where, f'quote {n} of {fid} must be {{"evidence_id": ..., "text": ...}}')
                    continue
                words = len(q.text.split())
                if q.evidence_id not in supplied[fid]:
                    add(8, where, f"quote {n} of {fid} is from {q.evidence_id!r}, which was not "
                        f"supplied for {fid}")  # fmt: skip
                elif _norm(q.text) not in text_of[q.evidence_id]:
                    add(8, where, f"quote {n} of {fid} (\"{q.text[:60]}\") is not word for word "
                        f"in {q.evidence_id}: copy the sentence exactly, without changing "
                        "words")  # fmt: skip
                elif not QUOTE_WORDS[0] <= words <= QUOTE_WORDS[1]:
                    add(8, where, f"quote {n} of {fid} has {words} words; quote one or two whole "
                        f"sentences ({QUOTE_WORDS[0]} to {QUOTE_WORDS[1]} words)")  # fmt: skip

    # rules 4, 6, 7 on every free-text field
    allowed = allowed_drugs(flags, patient_summary, finder)
    known = source_numbers(flags, evidence, patient_summary)
    for where, t, is_suggestion in texts(report):
        for raw, key in dict.fromkeys(finder.mentions(t)):
            if key not in allowed:
                add(4, where, f"{where} names {raw!r}, which is not prescribed or in a flag; "
                    "remove it")  # fmt: skip
        for why in instruction_problems(t, is_suggestion):
            add(6, where, f"{where} {why}. Rewrite it to describe the finding and what the "
                "evidence says, without telling anyone to stop, switch or avoid a drug or to "
                "change a dose")  # fmt: skip
        extra = sorted(numbers(t) - known)
        if extra:
            shown = ", ".join(f"{n:g}" for n in extra)
            add(7, where, f"{where} has numbers not in the flags or evidence ({shown}); remove "
                "them")  # fmt: skip
    return (report if not issues else None), issues
