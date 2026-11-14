"""Loads the v1 scope (drugs, genes, lab panels, lifestyle inputs) from scope/v1.yaml.

Every loader, test and evaluation reads the drug and gene list from here, never from its own copy.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator

from drug_safety.config import get_settings


class Scope(BaseModel):
    version: str
    status: str
    drugs: list[str]
    genes: list[str]
    lab_panels: list[str]
    lifestyle_inputs: list[str]

    @field_validator("drugs")
    @classmethod
    def drugs_lowercase_unique(cls, v: list[str]) -> list[str]:
        if any(d != d.strip().lower() for d in v):
            raise ValueError("drug names must be lowercase ingredient names")
        if len(set(v)) != len(v):
            raise ValueError("duplicate drug in scope")
        return v

    @field_validator("genes")
    @classmethod
    def genes_uppercase_unique(cls, v: list[str]) -> list[str]:
        if any(g != g.strip().upper() for g in v):
            raise ValueError("gene symbols must be uppercase")
        if len(set(v)) != len(v):
            raise ValueError("duplicate gene in scope")
        return v


def load_scope(path: Path | None = None) -> Scope:
    path = path or get_settings().scope_file
    with open(path, encoding="utf-8") as f:
        return Scope.model_validate(yaml.safe_load(f))
