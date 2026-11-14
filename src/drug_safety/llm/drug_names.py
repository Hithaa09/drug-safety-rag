"""Find drug names in free text (validator rule 4: no drug outside the prescriptions and flags).

    finder = DrugNameFinder.load(settings)
    finder.find("Plavix; prasugrel is an alternative")   # {"clopidogrel", "prasugrel"}

Names come from two places:
  - scope/drug_aliases.yaml: the v1 drugs with their synonyms and brands, mapped to one key
    ("Plavix" -> "clopidogrel", "acetaminophen" -> "paracetamol");
  - data/processed/drug_names.json: every prescribable RxNorm ingredient and brand
    (`make fetch-drug-names`), each its own key. Brands outside v1 are not mapped to their
    ingredient, so naming "Nexium" for an esomeprazole patient is allowed only because the
    prescription text also says "Nexium" (the caller runs find() on the prescriptions too).
Matching is whole-word and case-insensitive on word sequences, so "omeprazole" does not match
"esomeprazole", and punctuation inside a name is not significant ("Theo-24" = "Theo 24").
The longest name starting at a word wins ("sodium chloride" over "sodium").
Temporary until Person A's drug_alias table exists; then build the finder from that table.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from drug_safety.config import Settings

ALIASES_FILE = Path("scope/drug_aliases.yaml")


def names_path(settings: Settings) -> Path:
    """Written by `make fetch-drug-names` (drug_safety.ingest.rxnorm_names)."""
    return settings.data_dir / "processed" / "drug_names.json"


_WORD = re.compile(r"\w+")


def _words(text: str) -> tuple[str, ...]:
    return tuple(_WORD.findall(text.lower()))


class DrugNameFinder:
    def __init__(self, names: dict[str, str]):
        """names: lowercase name -> key."""
        self.names = names
        self._seq = {_words(n): k for n, k in names.items() if _words(n)}
        self._max = max(len(w) for w in self._seq)

    def mentions(self, text: str) -> list[tuple[str, str]]:
        """(as written, key) for each match, in order."""
        toks = list(_WORD.finditer(text))
        low = [t.group().lower() for t in toks]
        out, i = [], 0
        while i < len(toks):
            for n in range(min(self._max, len(toks) - i), 0, -1):
                key = self._seq.get(tuple(low[i : i + n]))
                if key is not None:
                    out.append((text[toks[i].start() : toks[i + n - 1].end()], key))
                    i += n
                    break
            else:
                i += 1
        return out

    @classmethod
    def load(cls, settings: Settings, aliases_file: Path = ALIASES_FILE) -> DrugNameFinder:
        path = names_path(settings)
        if not path.exists():
            raise FileNotFoundError(f"{path} missing: run `make fetch-drug-names`")
        body = json.loads(path.read_text())
        names = {n: n for n in body["ingredients"] + body["brands"]}
        for drug, aliases in yaml.safe_load(aliases_file.read_text())["aliases"].items():
            names |= {a.lower(): drug for a in [drug, *aliases]}  # v1 aliases win
        return cls(names)

    def find(self, text: str) -> set[str]:
        return {k for _, k in self.mentions(text)}
