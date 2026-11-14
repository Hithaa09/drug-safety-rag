"""Find which v1 drugs, genes and drug classes a text mentions, by whole-word dictionary match.

Reads scope/drug_aliases.yaml (temporary until Person A's drug_alias table exists) and the
class phrases in scope/drug_classes.yaml.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

ALIASES_FILE = Path("scope/drug_aliases.yaml")
CLASSES_FILE = Path("scope/drug_classes.yaml")


class Tagger:
    def __init__(
        self,
        drug_aliases: dict[str, list[str]],
        gene_aliases: dict[str, list[str]],
        class_phrases: dict[str, list[str]] | None = None,
    ):
        self._drugs = [(d, self._pattern(names)) for d, names in drug_aliases.items()]
        self._genes = [(g, self._pattern(names)) for g, names in gene_aliases.items()]
        self._classes = [(c, self._pattern(p)) for c, p in (class_phrases or {}).items()]

    @staticmethod
    def _pattern(names: list[str]) -> re.Pattern:
        # Whole words only: letters/digits on either side mean a different word
        # ("esomeprazole" must not count as "omeprazole"). Spaces in a name match any whitespace.
        alts = sorted((re.escape(n).replace(r"\ ", r"\s+") for n in names), key=len, reverse=True)
        return re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(alts) + r")(?![A-Za-z0-9])", re.I)

    def drugs(self, text: str) -> list[str]:
        return [d for d, p in self._drugs if p.search(text)]

    def genes(self, text: str) -> list[str]:
        return [g for g, p in self._genes if p.search(text)]

    def classes(self, text: str) -> list[str]:
        """Drug classes the text names ("NSAIDs", "other SSRIs"), as keys of drug_classes.yaml."""
        return [c for c, p in self._classes if p.search(text)]


@lru_cache
def default_tagger(path: Path = ALIASES_FILE, classes_path: Path = CLASSES_FILE) -> Tagger:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    with open(classes_path, encoding="utf-8") as f:
        phrases = {k: v["text"] for k, v in yaml.safe_load(f)["classes"].items()}
    return Tagger(raw["aliases"], raw["genes"], phrases)
