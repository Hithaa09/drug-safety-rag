"""Paper chunker: turn parsed PMC papers (full-text licence only) into evidence chunks.

Usage:
    python -m drug_safety.rag.chunk_papers run
    python -m drug_safety.rag.chunk_papers verify

Same design as the label chunker (rag/chunking.py): section-aware, packed to ~400 tokens, long
passages split at paragraph/sentence boundaries, at most 480 tokens by the embedding model's own
tokenizer, a context line on every chunk, drug/gene/class tags. Kept sections are decided in
ingest/pmc.py (abstract, results, discussion, conclusions, tables).

Unlike a label chunk, a paper chunk isn't automatically tagged with a drug: it gets the drugs
its own text (including the context line, which carries the paper's title) names.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.ingest.pmc import KEEP_SECTIONS, PaperDocument, PaperSection
from drug_safety.ingest.pmc import out_dir as papers_dir
from drug_safety.rag.chunking import (
    MAX_TOKENS,
    OVERLAP_TOKENS,
    SEPARATORS,
    TARGET_TOKENS,
    Chunk,
    TokenCounter,
    bge_token_counter,
    load_class_members,
)
from drug_safety.rag.tagging import Tagger, default_tagger
from drug_safety.scope import load_scope

CHUNKER_VERSION = "papers-v2"  # v2: a table is one unit (caption + body + footnotes)
LABEL = {
    "TITLE": "Title",
    "ABSTRACT": "Abstract",
    "RESULTS": "Results",
    "DISCUSS": "Discussion",
    "CONCL": "Conclusions",
    "TABLE": "Table",
}


CAPTION_KINDS = {"table_caption", "table_title_caption"}
FOOT_KINDS = {"table_footnote", "table_foot", "table_foot_title"}
HEAD_WORDS = 80  # caption + footnotes kept as the heading of every piece of the table


def table_units(sections: list[PaperSection]) -> list[PaperSection]:
    """BioC gives a table as separate passages (caption, body, footnotes). Chunked one by one,
    a caption whose table is too big to share its chunk ended up alone: 566 chunks of 7,551
    (papers-v1) were a caption or a footnote with no data. Here each table becomes one unit:
    caption and footnotes (abbreviations, units) as its heading, so every split piece repeats
    them, and the body as text. On their own line the text splitter cut them off into a piece
    with no data, so they cannot go in the text. Heading words past HEAD_WORDS are dropped
    (long footnote lists). A caption or footnote with no table body (17 papers' BioC has none)
    is dropped: without the data it is not evidence. Other passages pass through unchanged."""
    out, table = [], None

    def close():
        if table and table["body"]:
            words = " ".join(table["captions"] + table["foots"]).split()
            head = " ".join(words[:HEAD_WORDS]) or table["heading"]
            out.append(PaperSection(section="TABLE", kind="table", heading=head,
                                    text="\n".join(table["body"])))  # fmt: skip

    for s in sections:
        if s.section != "TABLE":
            close()
            table = None
            out.append(s)
        elif s.kind in CAPTION_KINDS:
            if table is None or table["body"] or table["foots"]:
                close()
                table = {"heading": s.heading, "captions": [], "body": [], "foots": []}
            table["captions"].append(s.text)
        else:
            if table is None or (s.kind not in FOOT_KINDS and table["body"]):
                close()
                table = {"heading": s.heading, "captions": [], "body": [], "foots": []}
            table["foots" if s.kind in FOOT_KINDS else "body"].append(s.text)
    close()
    return out


def _groups(doc: PaperDocument):
    groups = []
    for s in table_units(doc.sections):
        if groups and groups[-1][0].section == s.section:
            groups[-1].append(s)
        else:
            groups.append([s])
    return groups


def chunk_paper(
    doc: PaperDocument,
    count: TokenCounter,
    tagger: Tagger,
    load_id: str,
    class_members: dict[str, list[str]] | None = None,
) -> list[Chunk]:
    class_members = class_members or {}
    chunks: list[Chunk] = []
    per_section: dict[str, int] = {}
    title = doc.title if len(doc.title) <= 160 else doc.title[:157] + "..."

    def emit(sec: str, context: str, bodies: list[str], headings: list[list[str]]):
        text = context + "\n\n" + "\n\n".join(bodies)
        key = sec.lower()
        per_section[key] = per_section.get(key, 0) + 1
        classes = tagger.classes(text)
        chunks.append(
            Chunk(
                chunk_id=f"{doc.doc_id}:{key}:{per_section[key]:03d}",
                doc_id=doc.doc_id,
                source=doc.source,
                tier=doc.tier,
                section=key,
                headings=headings,
                text=text,
                drugs=tagger.drugs(text),
                genes=tagger.genes(text),
                drug_classes=classes,
                class_drugs=sorted({d for c in classes for d in class_members.get(c, [])}),
                token_count=count(text),
                source_version=doc.retrieved_at[:10],
                license=doc.license,
                chunker_version=CHUNKER_VERSION,
                load_id=load_id,
            )
        )

    tags = " · ".join(doc.drugs + doc.genes)
    for group in _groups(doc):
        sec = group[0].section
        label = LABEL.get(sec, sec)
        # The paper's drugs and genes (from selection: its title/abstract names them) go in the
        # context line, so a chunk is reachable by the drug filter and the tags are visible.
        context = f"paper | {tags} | {title} ({doc.pub_year or 'n.d.'}) | {label}"
        budget_target = TARGET_TOKENS - count(context) - 2
        budget_max = MAX_TOKENS - count(context) - 2

        def fit(text: str, prefix: str, budget_max: int = budget_max) -> list[str]:
            """Split `text` so that prefix + each piece stays within budget_max. Token counts
            don't add up exactly when pieces are joined, so every piece is re-checked and split
            again, smaller, until it fits."""
            size = budget_max - count(prefix)
            while True:
                splitter = RecursiveCharacterTextSplitter(
                    chunk_size=size,
                    chunk_overlap=min(OVERLAP_TOKENS, size // 4),
                    length_function=count,
                    separators=SEPARATORS,
                    keep_separator="end",
                )
                pieces = [prefix + piece for piece in splitter.split_text(text)]
                if all(count(x) <= budget_max for x in pieces):
                    return pieces
                size = int(size * 0.85)

        bodies, heads, size, last_heading = [], [], 0, None

        def unit_for(s, starting: bool, last_heading, label: str = label) -> str:
            show = (starting or s.heading != last_heading) and s.heading != label
            return f"{s.heading}\n{s.text}" if show else s.text

        for s in group:
            unit = unit_for(s, not bodies, last_heading)
            n = count(unit)
            if bodies and size + n + 2 > budget_target:
                emit(sec, context, bodies, heads)
                bodies, heads, size = [], [], 0
                unit = unit_for(s, True, last_heading)  # a new chunk starts with its heading
                n = count(unit)
            if n > budget_max:  # one passage too long for a chunk: split it
                prefix = f"{s.heading}\n" if s.heading != label else ""
                for body in fit(s.text, prefix):
                    emit(sec, context, [body], [[label, s.heading]])
                last_heading = None
                continue
            bodies.append(unit)
            heads.append([label, s.heading])
            size += n + 2
            last_heading = s.heading
        if bodies:
            emit(sec, context, bodies, heads)
    return chunks


def _paths(settings: Settings) -> tuple[Path, Path]:
    out = settings.data_dir / "processed" / "chunks"
    return out / "pmc.jsonl", out / "pmc_manifest.json"


def load_paper_docs(settings: Settings) -> list[PaperDocument]:
    pd = papers_dir(settings)
    manifest = json.loads((pd / "_manifest.json").read_text())
    return [
        PaperDocument.model_validate_json((pd / f"{m['pmcid']}.json").read_text())
        for m in manifest
        if m["status"] == "ok" and m["full_text"]
    ]


def run(settings: Settings, count: TokenCounter, tagger: Tagger) -> dict:
    members = load_class_members(settings)
    load_id = f"chunks-pmc-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    out, manifest_path = _paths(settings)
    out.parent.mkdir(parents=True, exist_ok=True)
    all_chunks, manifest = [], []
    for doc in load_paper_docs(settings):
        ch = chunk_paper(doc, count, tagger, load_id, members)
        all_chunks += ch
        manifest.append(
            {
                "doc_id": doc.doc_id,
                "tier": doc.tier,
                "chunks": len(ch),
                "untagged_chunks": sum(not c.drugs for c in ch),
            }
        )
    tmp = out.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(c.model_dump_json() + "\n")
    tmp.replace(out)
    manifest_path.write_text(json.dumps(manifest, indent=1))
    toks = sorted(c.token_count for c in all_chunks)
    return {
        "papers": len(manifest),
        "chunks": len(all_chunks),
        "tokens_min_median_max": [toks[0], toks[len(toks) // 2], toks[-1]] if toks else [],
        "chunks_without_a_drug_tag": sum(not c.drugs for c in all_chunks),
    }


def _overlap(prev: list[str], nxt: list[str], limit: int = 120) -> int:
    """Length of the longest end of `prev` that `nxt` starts with (the splitter's overlap)."""
    for k in range(min(len(prev), len(nxt), limit), 0, -1):
        if prev[-k:] == nxt[:k]:
            return k
    return 0


def read_paper_chunks(settings: Settings) -> list[Chunk]:
    with open(_paths(settings)[0], encoding="utf-8") as f:
        return [Chunk.model_validate_json(line) for line in f if line.strip()]


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


def verify(settings: Settings, count: TokenCounter, tagger: Tagger) -> list[Check]:
    checks: list[Check] = []

    def add(name, ok, detail=""):
        checks.append(Check(name=name, ok=bool(ok), detail="" if ok else str(detail)))

    chunks = read_paper_chunks(settings)
    docs = load_paper_docs(settings)
    by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.doc_id, []).append(c)
    ids = [c.chunk_id for c in chunks]
    add(
        "every full-text paper has chunks",
        all(d.doc_id in by_doc for d in docs),
        [d.doc_id for d in docs if d.doc_id not in by_doc][:5],
    )
    add(
        "chunks only from papers whose licence allows full text",
        set(by_doc) <= {d.doc_id for d in docs},
        set(by_doc) - {d.doc_id for d in docs},
    )
    add("chunk_ids unique", len(ids) == len(set(ids)), "duplicates")
    add(
        "chunk_id format pmc:PMCID:section:NNN",
        all(re.fullmatch(rf"{re.escape(c.doc_id)}:[a-z]+:\d{{3}}", c.chunk_id) for c in chunks),
        "bad id",
    )
    add(
        "fits the embedding model (<= 512 tokens)",
        all(count(c.text) + 2 <= 512 for c in chunks),
        "too long",
    )
    add(
        f"within chunk limit ({MAX_TOKENS})",
        all(c.token_count <= MAX_TOKENS for c in chunks),
        "over",
    )
    add(
        "stored token counts are correct",
        all(c.token_count == count(c.text) for c in chunks),
        "stale",
    )
    add("only kept sections", all(c.section.upper() in KEEP_SECTIONS for c in chunks), "other")
    scope = load_scope()
    add(
        "tags are v1 drugs and genes only",
        all(set(c.drugs) <= set(scope.drugs) and set(c.genes) <= set(scope.genes) for c in chunks),
        "bad tag",
    )
    add(
        "tags match the current dictionaries",
        all(
            c.drugs == tagger.drugs(c.text)
            and c.genes == tagger.genes(c.text)
            and c.drug_classes == tagger.classes(c.text)
            for c in chunks
        ),
        "rerun",
    )
    # Nothing lost: every run of 8 consecutive words of a kept section appears whole inside
    # some chunk of that paper. (Table rows have no sentence breaks, so a sentence check can't
    # tell "split across two chunks" from "lost"; word windows can.)
    missing = []
    dropped = Counter()
    for d in docs:
        windows = set()
        for c in by_doc.get(d.doc_id, []):
            w = c.text.split()
            windows |= {" ".join(w[i : i + 8]) for i in range(max(1, len(w) - 7))}
        # The paper's chunk bodies in order, without context lines and repeated headings, so a
        # window that straddles two consecutive chunks (no overlap at a sentence break) is found.
        heads = {h for c in by_doc.get(d.doc_id, []) for hs in c.headings for h in hs}
        joined: list[str] = []
        for c in by_doc.get(d.doc_id, []):
            words = " ".join(
                line for line in c.text.split("\n")[1:] if line.strip() not in heads
            ).split()
            joined += words[_overlap(joined, words) :]  # drop the repeated overlap
        body = " ".join(joined)
        units = table_units(d.sections)
        dropped["tables without a body"] += sum(
            1 for s in d.sections if s.kind in CAPTION_KINDS
        ) - sum(1 for u in units if u.section == "TABLE")
        # a table's caption and footnotes are its heading: check them as text too
        texts = [u.text for u in units] + [u.heading for u in units if u.section == "TABLE"]
        for text in texts:
            w = text.split()
            if len(w) < 8:  # too short for a window: the whole text must be in a chunk
                full = " ".join(" ".join(c.text.split()) for c in by_doc.get(d.doc_id, []))
                if " ".join(w) not in full:
                    missing.append((d.external_id, " ".join(w)[:50]))
                continue
            for i in range(0, len(w) - 7, 4):
                win = " ".join(w[i : i + 8])
                if win not in windows and win not in body:
                    missing.append((d.external_id, win[:50]))
                    break
    add(
        "no text lost from kept sections (8-word windows)",
        not missing,
        f"{len(missing)} sections, e.g. {missing[:2]}",
    )
    members = load_class_members(settings)
    again = [c for d in docs for c in chunk_paper(d, count, tagger, "x", members)]
    add(
        "up to date (re-chunking gives identical output)",
        [(c.chunk_id, c.text) for c in again] == [(c.chunk_id, c.text) for c in chunks],
        "rerun",
    )
    add("report: left out on purpose (a caption with no table data is not evidence)", True)
    checks[-1].detail = f"{dropped['tables without a body']} tables without a body"
    untagged = sum(not c.drugs for c in chunks)
    add("report: chunks naming no v1 drug (unreachable by the drug filter; not a failure)", True)
    checks[-1].detail = f"{untagged} of {len(chunks)}"
    return checks


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    p = argparse.ArgumentParser(prog="python -m drug_safety.rag.chunk_papers")
    p.add_argument("cmd", choices=["run", "verify"])
    args = p.parse_args(argv)
    count, tagger = bge_token_counter(settings.embedding_model), default_tagger()
    if args.cmd == "run":
        print(json.dumps(run(settings, count, tagger), indent=2))
        return 0
    checks = verify(settings, count, tagger)
    failed = [c for c in checks if not c.ok]
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name}" + (f": {c.detail}" if c.detail else ""))
    print("RESULT:", "PASS" if not failed else "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
