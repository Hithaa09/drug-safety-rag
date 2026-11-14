"""Template report built from the flags alone (spec p16: used when the LLM fails twice or the
model server is down). Deterministic, so it can never invent anything: every sentence is the
flag's own fields. It cites the evidence supplied for each flag so the interface can still link
the passages. tests/llm and `make verify-llm` check that it passes the validator.
"""

from __future__ import annotations

from drug_safety.contracts import EvidenceChunk, Flag, FlagExplanation, Report, Suggestion
from drug_safety.llm.prompts import trigger

TYPE_LABEL = {
    "drug_drug": "drug interaction",
    "gene_drug": "genotype",
    "lab_drug": "lab value",
    "lifestyle_drug": "lifestyle",
}


def fallback_report(flags: list[Flag], evidence: dict[str, list[EvidenceChunk]]) -> Report:
    ids = {f.flag_id: [c.chunk_id for c in evidence.get(f.flag_id, [])] for f in flags}
    listed = "; ".join(f"{f.drug} ({TYPE_LABEL[f.flag_type]}, {f.severity})" for f in flags)
    n = len(flags)
    summary = (
        f"{n} flag{'s' if n != 1 else ''} raised for review: {listed}."
        if flags
        else "No flags were raised."
    )
    return Report(
        summary=summary,
        flags_explained=[
            FlagExplanation(
                flag_id=f.flag_id,
                plain_explanation=(
                    f"{f.reason.rstrip('.')}. Raised by a {f.source} rule for {f.drug} "
                    f"({trigger(f)}); severity {f.severity}."
                ),
                evidence_ids=ids[f.flag_id],
            )
            for f in flags
        ],
        suggestions_for_clinician_review=[
            Suggestion(
                text=f"Consider reviewing {f.drug} in light of this {TYPE_LABEL[f.flag_type]} "
                "flag and its evidence.",
                flag_id=f.flag_id,
                evidence_ids=ids[f.flag_id],
            )
            for f in flags
            if ids[f.flag_id]
        ],
        uncertainties=[
            f"No evidence passage was found for {f.flag_id} ({f.drug})."
            for f in flags
            if not ids[f.flag_id]
        ],
    )
