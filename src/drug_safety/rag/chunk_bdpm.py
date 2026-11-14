"""Acenocoumarol label passages (BDPM, English translation) -> evidence chunks.

Usage:
    python -m drug_safety.rag.chunk_bdpm run      # reviewed passages -> chunks/bdpm.jsonl
    python -m drug_safety.rag.chunk_bdpm sheet    # side-by-side sheet for the reviewer
    python -m drug_safety.rag.chunk_bdpm verify   # offline checks

One chunk per reviewed passage, written like a DailyMed label chunk (its first line names the
drug, so the retriever's "own label first" rule applies). Unreviewed passages never become
chunks: the translation must be checked by a person who reads French first (Person A, E2).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.ingest.bdpm import (
    SOURCE,
    TRANSLATIONS,
    Passage,
    Translations,
    french_mismatches,
    load_translations,
    page_text,
    raw_path,
    updated_on_page,
)
from drug_safety.rag.chunking import (
    MAX_TOKENS,
    Chunk,
    TokenCounter,
    bge_token_counter,
    load_class_members,
)
from drug_safety.rag.tagging import default_tagger

SHEET = Path("docs/translation_review_sintrom.md")


class BdpmDocument(BaseModel):
    doc_id: str
    source: str = SOURCE
    external_id: str
    title: str
    drugs: list[str]
    study_type: str = "label"
    tier: int = 1
    pub_year: int | None = None
    license: str
    raw_path: str
    source_version: str
    retrieved_at: str
    labeler: str | None = None


def out_path(settings: Settings) -> Path:
    return settings.data_dir / "processed" / "chunks" / "bdpm.jsonl"


def licence(t: Translations, p: Passage) -> str:
    return (f"{t.source.licence}; source: BDPM (ANSM), {t.source.name}, updated "
            f"{t.source.updated}; English translation reviewed by {p.reviewed_by} on "
            f"{p.reviewed_on}")  # fmt: skip


def document(settings: Settings, t: Translations) -> BdpmDocument:
    rp = raw_path(settings, t.source.cis)
    return BdpmDocument(
        doc_id=f"{SOURCE}:{t.source.cis}", external_id=t.source.cis,
        title=f"{t.source.name} (France, BDPM; English translation)",
        drugs=[t.source.substance],
        license=f"{t.source.licence}; source: BDPM (ANSM), updated {t.source.updated}",
        raw_path=str(rp), source_version=f"ANSM update {t.source.updated}",
        retrieved_at=rp.with_suffix(".fetched_at").read_text().strip(),
    )  # fmt: skip


def chunk_text(t: Translations, p: Passage) -> str:
    return (f"{t.source.substance} | {t.source.name} (France; translated from French) | "
            f"{p.section}\n\n{p.english}")  # fmt: skip


def chunks(settings: Settings, t: Translations, count: TokenCounter) -> list[Chunk]:
    tagger = default_tagger()
    members = load_class_members(settings)
    load_id = f"chunks-bdpm-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    out = []
    for p in (x for x in t.passages if x.reviewed):
        text = chunk_text(t, p)
        classes = tagger.classes(text)
        out.append(Chunk(
            chunk_id=f"{SOURCE}:{t.source.cis}:{p.section_key}:{p.id}",
            doc_id=f"{SOURCE}:{t.source.cis}", source=SOURCE, tier=1, section=p.section_key,
            headings=[[p.section]], text=text,
            drugs=sorted(set(tagger.drugs(text)) | {t.source.substance}),
            genes=tagger.genes(text), drug_classes=classes,
            class_drugs=sorted({d for c in classes for d in members.get(c, [])}),
            token_count=count(text), source_version=f"ANSM update {t.source.updated}",
            license=licence(t, p), load_id=load_id,
        ))  # fmt: skip
    return out


def run(settings: Settings, count: TokenCounter) -> dict:
    t = load_translations()
    cs = chunks(settings, t, count)
    p = out_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(c.model_dump_json() + "\n" for c in cs), encoding="utf-8")
    return {"passages": len(t.passages), "reviewed": len(cs), "chunks": len(cs)}


def read_bdpm_chunks(settings: Settings) -> list[Chunk]:
    with open(out_path(settings), encoding="utf-8") as f:
        return [Chunk.model_validate_json(line) for line in f if line.strip()]


def load_bdpm_docs(settings: Settings) -> list[BdpmDocument]:
    """The document, only once it has a reviewed passage (no document without chunks)."""
    return [document(settings, load_translations())] if read_bdpm_chunks(settings) else []


def sheet(t: Translations) -> str:
    L = ["# Translation review: acenocoumarol (SINTROM 4 mg) label, French -> English", "",
         f"Source: [{t.source.name}]({t.source.url}), BDPM, ANSM update {t.source.updated}, "
         f"{t.source.licence}.", "",
         "**For the reviewer (someone who reads French):** for each passage, check that the "
         "English says exactly what the French says, with nothing added or lost. Then, in "
         f"`{TRANSLATIONS}`, set `reviewed: true`, `reviewed_by` and `reviewed_on`, or correct "
         "`english` first. Only reviewed passages become evidence. `[…]` marks list items left "
         "out on purpose (see \"Omitted\").", ""]  # fmt: skip
    for p in t.passages:
        state = f"reviewed by {p.reviewed_by} on {p.reviewed_on}" if p.reviewed else "not reviewed"
        L += [f"## {p.section} ({p.id}): {state}", "", "| French | English |", "|---|---|"]
        fr, en = p.french.splitlines(), p.english.splitlines()
        for f_line, e_line in zip(fr, en, strict=False):
            L.append(
                f"| {f_line.replace('|', chr(92) + '|')} | {e_line.replace('|', chr(92) + '|')} |"
            )
        if p.omitted:
            L += ["", f"Omitted: {p.omitted}"]
        L += ["", f"- [{'x' if p.reviewed else ' '}] English matches the French", ""]
    return "\n".join(L)


# ---------------------------------------------------------------- verify


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


def verify(settings: Settings, count: TokenCounter) -> list[Check]:
    checks: list[Check] = []

    def add(name, ok, detail=""):
        checks.append(Check(name=name, ok=bool(ok), detail="" if ok else str(detail)))

    t = load_translations()
    page = page_text(raw_path(settings, t.source.cis).read_text(encoding="utf-8",
                                                                errors="replace"))  # fmt: skip
    add("page stored, with the update date the translations cite",
        updated_on_page(page) == t.source.updated, updated_on_page(page))  # fmt: skip
    bad = french_mismatches(t, page)
    add("every French line is on the page word for word", not bad, bad[:3])
    add(
        "every passage has an English translation of similar length",
        all(0.6 < len(p.english) / len(p.french) < 1.5 for p in t.passages),
        [p.id for p in t.passages if not 0.6 < len(p.english) / len(p.french) < 1.5],
    )
    add("passages marked '[…]' say what was omitted",
        all(p.omitted for p in t.passages if "[…]" in p.french))  # fmt: skip
    add(
        "French and English have the same number of lines (line-by-line review)",
        all(len(p.french.splitlines()) == len(p.english.splitlines()) for p in t.passages),
    )
    reviewed = [p for p in t.passages if p.reviewed]
    add("every reviewed passage names its reviewer and date",
        all(p.reviewed_by and p.reviewed_on for p in reviewed))  # fmt: skip
    cs = read_bdpm_chunks(settings)
    add("chunks exactly for the reviewed passages",
        sorted(c.chunk_id.rsplit(":", 1)[1] for c in cs) == sorted(p.id for p in reviewed),
        f"{len(cs)} chunks, {len(reviewed)} reviewed")  # fmt: skip
    add(f"within chunk limit ({MAX_TOKENS} tokens)",
        all(count(chunk_text(t, p)) <= MAX_TOKENS for p in t.passages))  # fmt: skip
    add("review sheet up to date", SHEET.exists() and SHEET.read_text() == sheet(t) + "\n",
        "run `make bdpm-sheet`")  # fmt: skip
    add(f"translations reviewed: {len(reviewed)} of {len(t.passages)}", True)
    return checks


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    p = argparse.ArgumentParser(prog="python -m drug_safety.rag.chunk_bdpm")
    p.add_argument("cmd", choices=["run", "sheet", "verify"])
    args = p.parse_args(argv)
    if args.cmd == "sheet":
        SHEET.write_text(sheet(load_translations()) + "\n", encoding="utf-8")
        print(f"written {SHEET}")
        return 0
    count = bge_token_counter(settings.embedding_model)
    if args.cmd == "run":
        print(json.dumps(run(settings, count), indent=1))
        return 0
    checks = verify(settings, count)
    failed = [c for c in checks if not c.ok]
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name}" + (f": {c.detail}" if c.detail else ""))
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed.")
    print("RESULT:", "PASS" if not failed else "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
