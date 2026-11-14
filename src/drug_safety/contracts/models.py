"""Shared contracts: Patient (API input), Flag (rule engine -> RAG/LLM), Report (LLM output),
and the API response that bundles them.

Drafted by Person B; agreed with Person A on 2026-11-14 (docs/questions_for_person_a.md, A1-A9;
A7, alcohol amount, still open).
Depends on pydantic only. Every model rejects unknown fields, so a typo or an invented field
(for example from the LLM) fails loudly instead of being ignored.

Drugs are lowercase ingredient names (e.g. "clopidogrel"), matching the evidence chunks. Each
place that names a drug also has an optional `rxcui`, so Person A can switch joins to RxCUIs.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CONTRACTS_VERSION = "0.1.0"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------- shared enums


class Severity(StrEnum):
    """Spec scale, highest first. Use `Severity.rank` to sort."""

    contraindicated = "contraindicated"
    major = "major"
    moderate = "moderate"
    minor = "minor"

    @property
    def rank(self) -> int:  # 0 = most severe
        return list(Severity).index(self)


class FlagType(StrEnum):
    drug_drug = "drug_drug"
    gene_drug = "gene_drug"
    lab_drug = "lab_drug"
    lifestyle_drug = "lifestyle_drug"


# ---------------------------------------------------------------- Patient (API input)


class Prescription(_Strict):
    name: str = Field(description="As entered: brand, generic or NDC, e.g. 'Plavix 75'")
    dose: str | None = None
    frequency: str | None = None
    status: Literal["new", "ongoing"] | None = Field(
        None,
        description="Starting the drug now, or already taking it. Some labels differ (clozapine: "
        "ANC rules for starting it are stricter than for continuing). Optional (A6)",
    )


class LabResult(_Strict):
    test: str = Field(description="Test name or LOINC code as entered, e.g. 'creatinine'")
    value: float
    unit: str
    taken_at: str | None = Field(None, description="ISO date; the latest value per test is used")


class Genotype(_Strict):
    gene: str = Field(description="Gene symbol, e.g. 'CYP2C19'")
    diplotype: str = Field(description="Star-allele pair, e.g. '*2/*2'")


class Diet(_Strict):
    type: Literal["veg", "non_veg"] | None = None
    meals: list[str] = Field(
        default_factory=list, description="Free-text meals; normalisation maps them to diet tags"
    )


class Lifestyle(_Strict):
    smoking: Literal["never", "former", "current"] | None = None
    alcohol: Literal["none", "occasional", "regular", "heavy"] | None = None
    diet: Diet = Field(default_factory=Diet)


class Patient(_Strict):
    patient_id: str
    age: int = Field(ge=0, le=120, description="Years; needed for eGFR (CKD-EPI 2021)")
    sex: Literal["female", "male"] = Field(description="Needed for eGFR (CKD-EPI 2021)")
    prescriptions: list[Prescription] = Field(min_length=1)
    labs: list[LabResult] = Field(default_factory=list)
    lifestyle: Lifestyle = Field(default_factory=Lifestyle)
    genotypes: list[Genotype] = Field(default_factory=list)
    weight_kg: float | None = Field(
        None,
        gt=0,
        le=400,
        description="Body weight. Lets renal rules use creatinine clearance (Cockcroft-Gault) as "
        "many labels do; without it they use eGFR and say so. Optional (A8)",
    )
    conditions: list[Literal["benign_ethnic_neutropenia"]] = Field(
        default_factory=list,
        description="Conditions that change a rule's threshold. benign_ethnic_neutropenia: "
        "clozapine's ANC thresholds are lower (Duffy-null). Optional (A9)",
    )


# ---------------------------------------------------------------- Flag (rule engine output)


class LabFinding(_Strict):
    measure: str = Field(description="LOINC code or derived measure, e.g. 'egfr'")
    value: float
    unit: str
    operator: Literal["<", "<=", ">", ">="]
    threshold: float


class LifestyleFactor(_Strict):
    factor_type: Literal["smoking", "alcohol", "diet_tag"]
    factor_value: str = Field(description="e.g. 'current', 'heavy', 'grapefruit'")


_RULE_ID = re.compile(r"^(drug_drug|gene_drug|lab_drug|lifestyle_drug):.+")
_FLAG_ID = re.compile(r"^[a-z]+-\d{3}$")


class Flag(_Strict):
    """One unsafe finding. The seven spec fields (p14) come first; the rest identify what
    triggered the flag, which the retriever needs to build its query and drug filter."""

    flag_id: str = Field(description="e.g. 'gene-001'; unique within one assessment")
    flag_type: FlagType
    drug: str = Field(description="Ingredient name of the flagged drug")
    severity: Severity
    rule_id: str = Field(
        description="'<flag_type>:...', e.g. 'gene_drug:CYP2C19:poor_metabolizer:clopidogrel'"
    )
    reason: str
    source: str = Field(description="Where the rule came from, e.g. 'CPIC', 'DDInter', 'label'")
    # What triggered it (required per flag type, see the validator).
    interacting_drug: str | None = Field(None, description="drug_drug: the other drug")
    gene: str | None = Field(None, description="gene_drug: gene symbol")
    phenotype: str | None = Field(None, description="gene_drug: e.g. 'poor metabolizer'")
    lab: LabFinding | None = Field(None, description="lab_drug: the value that crossed the rule")
    lifestyle: LifestyleFactor | None = Field(None, description="lifestyle_drug: the factor")
    rxcui: str | None = Field(None, description="Optional ingredient RxCUI of `drug`")

    @model_validator(mode="after")
    def _consistent(self) -> Flag:
        if not _FLAG_ID.match(self.flag_id):
            raise ValueError(f"flag_id {self.flag_id!r} must look like 'gene-001'")
        if not _RULE_ID.match(self.rule_id) or not self.rule_id.startswith(f"{self.flag_type}:"):
            raise ValueError(f"rule_id {self.rule_id!r} must start with '{self.flag_type}:'")
        need = {
            FlagType.drug_drug: ("interacting_drug",),
            FlagType.gene_drug: ("gene", "phenotype"),
            FlagType.lab_drug: ("lab",),
            FlagType.lifestyle_drug: ("lifestyle",),
        }[self.flag_type]
        missing = [f for f in need if getattr(self, f) is None]
        if missing:
            raise ValueError(f"{self.flag_type} flag needs {', '.join(missing)}")
        return self

    def drug_ids(self) -> list[str]:
        """Drugs for the retriever's filter: both drugs for an interaction (spec RAG step 2)."""
        return [self.drug] + ([self.interacting_drug] if self.interacting_drug else [])

    def query_text(self) -> str:
        """Retrieval query from drug, gene or lab, and reason (spec RAG step 1)."""
        parts = [*self.drug_ids()]
        if self.gene:
            parts += [self.gene, self.phenotype or ""]
        if self.lab:
            parts.append(self.lab.measure)
        if self.lifestyle:
            parts.append(self.lifestyle.factor_value)
        parts.append(self.reason)
        return " ".join(p for p in parts if p)


class DataGap(_Strict):
    """A missing input that made the engine skip rules (spec: never a flag, always reported)."""

    kind: Literal["lab", "genotype", "lifestyle", "drug"]
    message: str = Field(description="e.g. 'No creatinine value, renal rules skipped'")


# ---------------------------------------------------------------- Report (LLM output, spec p16)


class FlagExplanation(_Strict):
    flag_id: str
    plain_explanation: str = Field(min_length=1)
    evidence_ids: list[str] = Field(
        description="Chunk IDs. May be empty only if the flag had no evidence; that must then "
        "be stated under `uncertainties` (spec prompt rules)"
    )


class Suggestion(_Strict):
    text: str = Field(min_length=1, description="Worded as 'Consider reviewing ...'")
    flag_id: str
    evidence_ids: list[str] = Field(
        min_length=1, description="Spec validator rule 5: every suggestion cites evidence"
    )


class Report(_Strict):
    """Exactly the JSON Qwen must return (spec p16). Extra fields are rejected."""

    summary: str = Field(min_length=1)
    flags_explained: list[FlagExplanation]
    suggestions_for_clinician_review: list[Suggestion]
    uncertainties: list[str]


# ---------------------------------------------------------------- API (spec p17)


class EvidenceChunk(_Strict):
    """One retrieved passage, as shown to Qwen and in the interface."""

    chunk_id: str
    doc_id: str
    source: str
    tier: int = Field(ge=1, le=4)
    title: str | None = None
    heading: str | None = None
    text: str
    license: str


class AssessRequest(_Strict):
    patient: Patient
    use_llm: bool = Field(True, description="False returns flags and evidence only")


class AssessResponse(_Strict):
    contracts_version: str = CONTRACTS_VERSION
    patient_id: str
    flags: list[Flag] = Field(description="Sorted by severity, most severe first")
    evidence: dict[str, list[EvidenceChunk]] = Field(description="flag_id -> its chunks")
    data_gaps: list[DataGap]
    report: Report | None
    report_source: Literal["llm", "template", "none"] = Field(
        description="'template' when the LLM output failed validation twice (spec fallback); "
        "'none' when use_llm was false"
    )
    notice: str | None = Field(None, description="e.g. 'AI summary unavailable'")
    disclaimer: str = "Decision support for a pharmacist or clinician, not medical advice."

    @model_validator(mode="after")
    def _consistent(self) -> AssessResponse:
        ids = [f.flag_id for f in self.flags]
        if len(ids) != len(set(ids)):
            raise ValueError("flag_ids must be unique")
        if [f.severity.rank for f in self.flags] != sorted(f.severity.rank for f in self.flags):
            raise ValueError("flags must be sorted by severity, most severe first")
        if not set(self.evidence) <= set(ids):
            raise ValueError("evidence keys must be flag_ids from `flags`")
        if (self.report is None) != (self.report_source == "none"):
            raise ValueError("report must be present unless report_source is 'none'")
        return self
