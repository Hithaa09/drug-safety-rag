"""Acenocoumarol label from the French public medicines database (BDPM), with a reviewed English
translation (Person A, question E2, 2026-11-14). Acenocoumarol has no US label.

Usage:
    python -m drug_safety.ingest.bdpm fetch    # network: 1 page (SINTROM 4 mg, CIS 61510352)

The passages used, their French text and the English translation are in
curation/translations/sintrom_bdpm_61510352.yaml. The French is checked word for word against
the stored page (rag/chunk_bdpm.py verify). Only passages a person has marked `reviewed: true`
become evidence.

Licence: Licence Ouverte / Open Licence 2.0 (Etalab), linked from every BDPM page. It allows
reuse and adaptation, including translation, for any purpose; credit the source and the date of
last update ("ANSM - Mis à jour le : 20/06/2025").
"""

from __future__ import annotations

import argparse
import html
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.ingest.dailymed import USER_AGENT

SOURCE = "bdpm"
TRANSLATIONS = Path("curation/translations/sintrom_bdpm_61510352.yaml")


class Passage(BaseModel):
    id: str
    section: str
    section_key: str
    french: str
    english: str
    omitted: str = ""
    reviewed: bool = False
    reviewed_by: str | None = None
    reviewed_on: str | None = None


class SourceInfo(BaseModel):
    name: str
    substance: str
    cis: str
    url: str
    updated: str
    licence: str


class Translations(BaseModel):
    source: SourceInfo
    passages: list[Passage]


def load_translations(path: Path = TRANSLATIONS) -> Translations:
    return Translations.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def raw_path(settings: Settings, cis: str) -> Path:
    return settings.raw_dir / SOURCE / f"{cis}.html"


def fetch(settings: Settings, t: Translations | None = None) -> Path:
    t = t or load_translations()
    r = httpx.get(t.source.url, timeout=60, follow_redirects=True,
                  headers={"User-Agent": USER_AGENT})  # fmt: skip
    r.raise_for_status()
    p = raw_path(settings, t.source.cis)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(r.content)
    p.with_suffix(".fetched_at").write_text(datetime.now(UTC).isoformat(timespec="seconds"))
    return p


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace(" ", " ")).strip()


def page_text(html_text: str) -> str:
    """The page as plain text, one paragraph per line, whitespace normalised."""
    paras = re.findall(r"<p\b[^>]*>(.*?)</p>", html_text, re.S)
    return "\n".join(
        _norm(html.unescape(re.sub(r"<[^>]+>", "", p)).replace("·", ""))
        for p in paras
    )  # fmt: skip


def french_mismatches(t: Translations, page: str) -> list[str]:
    """French lines not found word for word on the page ("[…]" marks an omission)."""
    return [f"{p.id}: {line[:70]}" for p in t.passages for line in p.french.splitlines()
            if line.strip() and line.strip() != "[…]" and _norm(line) not in page]  # fmt: skip


def updated_on_page(page: str) -> str | None:
    """'ANSM - Mis à jour le : 20/06/2025' -> '2025-06-20'."""
    m = re.search(r"Mis à jour le\s*:\s*(\d{2})/(\d{2})/(\d{4})", page)
    return f"{m[3]}-{m[2]}-{m[1]}" if m else None


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    p = argparse.ArgumentParser(prog="python -m drug_safety.ingest.bdpm")
    p.add_argument("cmd", choices=["fetch"])
    p.parse_args(argv)
    t = load_translations()
    path = fetch(settings, t)
    page = page_text(path.read_text(encoding="utf-8", errors="replace"))
    print(f"saved {path}; page updated {updated_on_page(page)}; "
          f"French lines not found: {len(french_mismatches(t, page))}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
