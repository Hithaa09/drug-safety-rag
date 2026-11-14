"""What the result screen shows, worked out from an AssessResponse without Streamlit, so every
rule of the layout is unit-tested (spec p17: flag table sorted by severity; summary and
suggestions; each explanation linked to its expandable evidence and source; data gaps as notes).
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from drug_safety.contracts import AssessResponse, EvidenceChunk, Flag, Suggestion

DISCLAIMER = "Decision support for a pharmacist or clinician, not medical advice."

SEVERITY_ICON = {"contraindicated": "🟥", "major": "🟧", "moderate": "🟨", "minor": "⬜"}
TYPE_LABEL = {
    "drug_drug": "Drug-drug",
    "gene_drug": "Gene-drug",
    "lab_drug": "Lab-drug",
    "lifestyle_drug": "Lifestyle-drug",
}
SOURCE_LABEL = {
    "dailymed": "DailyMed label",
    "pmc": "PubMed Central paper",
    "pubmed": "PubMed",
    "cpic": "CPIC guideline",
    "dpwg": "DPWG guideline (ClinPGx)",
    "bdpm": "Translated label (France, BDPM)",
}
REPORT_SOURCE = {
    "llm": "Written by the AI model and checked by the safety validator against the evidence.",
    "template": "Built from the flags alone (no AI wording).",
    "none": "AI summary turned off: flags and evidence only.",
}


def trigger(f: Flag) -> str:
    if f.interacting_drug:
        return f"with {f.interacting_drug}"
    if f.gene:
        return f"{f.gene} {f.phenotype}"
    if f.lab:
        x = f.lab
        return f"{x.measure} {x.value:g} {x.unit} (rule {x.operator} {x.threshold:g})"
    if f.lifestyle:
        return f"{f.lifestyle.factor_type.replace('_', ' ')}: {f.lifestyle.factor_value}"
    return ""


def sorted_flags(r: AssessResponse) -> list[Flag]:
    """Most severe first (the API sorts already; sorting again costs nothing)."""
    return sorted(r.flags, key=lambda f: f.severity.rank)


def flag_table(r: AssessResponse) -> list[dict]:
    """Spec columns (drug, type, severity, reason) plus what triggered it."""
    return [
        {
            "Severity": f"{SEVERITY_ICON[f.severity]} {f.severity}",
            "Drug": f.drug,
            "Type": TYPE_LABEL[f.flag_type],
            "Reason": f.reason,
            "Triggered by": trigger(f),
            "Rule source": f.source,
            "Flag": f.flag_id,
        }
        for f in sorted_flags(r)
    ]


class Cited(BaseModel):
    number: int  # [1], [2] ... in reading order on the page
    chunk: EvidenceChunk


class FlagView(BaseModel):
    flag: Flag
    explanation: str | None  # None when there is no report, or (a bug) the flag was skipped
    cited: list[Cited]  # evidence the explanation cites, in cited order
    other: list[Cited]  # retrieved for this flag but not cited
    missing_ids: list[str]  # cited but not in the evidence (should never happen; shown, not hidden)
    suggestions: list[tuple[str, list[int]]]  # text, numbers of the evidence it cites


def flag_views(r: AssessResponse) -> list[FlagView]:
    explained = {e.flag_id: e for e in (r.report.flags_explained if r.report else [])}
    suggestions = r.report.suggestions_for_clinician_review if r.report else []
    out = []
    n = 0
    for f in sorted_flags(r):
        chunks = {c.chunk_id: c for c in r.evidence.get(f.flag_id, [])}
        e = explained.get(f.flag_id)
        cited_ids = list(dict.fromkeys(e.evidence_ids)) if e else []
        cited, missing = [], []
        for cid in cited_ids:
            if cid in chunks:
                n += 1
                cited.append(Cited(number=n, chunk=chunks[cid]))
            else:
                missing.append(cid)
        other = []
        for cid, c in chunks.items():
            if cid not in cited_ids:
                n += 1
                other.append(Cited(number=n, chunk=c))
        numbers = {c.chunk.chunk_id: c.number for c in cited + other}
        out.append(
            FlagView(
                flag=f,
                explanation=e.plain_explanation if e else None,
                cited=cited,
                other=other,
                missing_ids=missing,
                suggestions=[
                    (s.text, [numbers[i] for i in s.evidence_ids if i in numbers])
                    for s in suggestions
                    if s.flag_id == f.flag_id
                ],
            )
        )
    return out


def all_suggestions(r: AssessResponse) -> list[Suggestion]:
    return r.report.suggestions_for_clinician_review if r.report else []


def source_line(c: EvidenceChunk) -> str:
    """'DailyMed label · PLAVIX ... · 12.5 Pharmacogenomics · tier 1'."""
    parts = [SOURCE_LABEL.get(c.source, c.source), c.title, c.heading, f"tier {c.tier}"]
    return " · ".join(p for p in parts if p)


_PMC = re.compile(r"^pmc:(PMC\d+)$")
_PUBMED = re.compile(r"^pubmed:(\d+)$")
_SETID = re.compile(r"^dailymed:([0-9a-f-]{36})$")
_CPIC = re.compile(r"^cpic:(PA\d+)$")
_DPWG = re.compile(r"^dpwg:(PA\d+)$")
_BDPM = re.compile(r"^bdpm:(\d{8})$")


def source_url(c: EvidenceChunk) -> str | None:
    """Public page of the document the passage comes from."""
    if m := _SETID.match(c.doc_id):
        return f"https://dailymed.nlm.nih.gov/dailymed/drugInfo.cfm?setid={m[1]}"
    if m := _PMC.match(c.doc_id):
        return f"https://pmc.ncbi.nlm.nih.gov/articles/{m[1]}/"
    if m := _PUBMED.match(c.doc_id):
        return f"https://pubmed.ncbi.nlm.nih.gov/{m[1]}/"
    if m := _CPIC.match(c.doc_id):  # CPIC guideline pages now live on ClinPGx
        return f"https://www.clinpgx.org/guideline/{m[1]}"
    if m := _DPWG.match(c.doc_id):
        return f"https://www.clinpgx.org/guidelineAnnotation/{m[1]}"
    if m := _BDPM.match(c.doc_id):  # the French original
        return f"https://base-donnees-publique.medicaments.gouv.fr/medicament/{m[1]}/extrait"
    return None


_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()<>#+\-.!|~$])")


def md(text: str) -> str:
    """Escape text from the API (model output, evidence) before st.markdown, so a passage or an
    answer cannot inject links, images or formatting."""
    return _MD_SPECIAL.sub(r"\\\1", text)
