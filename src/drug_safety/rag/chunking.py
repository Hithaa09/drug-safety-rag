"""Label chunker: turn parsed DailyMed labels into evidence chunks ready for embedding.

Usage:
    python -m drug_safety.rag.chunking run       # chunk every parsed label
    python -m drug_safety.rag.chunking verify    # offline acceptance checks

Reads:   data/processed/dailymed/<setid>.json        (from the DailyMed loader)
Writes:  data/processed/chunks/dailymed.jsonl         one chunk per line (future kb.evidence_chunks)
         data/processed/chunks/dailymed_manifest.json  per-label counts

How a label is chunked:
  1. Keep only clinically useful sections (KEEP_SECTIONS); drop patient leaflets, descriptions,
     packaging, trial narratives. An unknown section type stops the run until it is classified.
  2. Within one top-level section, pack consecutive subsections together up to TARGET_TOKENS,
     each subsection starting with its own heading line. Never mix two top-level sections.
  3. A subsection longer than the limit is split at paragraph, line, then sentence boundaries,
     with OVERLAP_TOKENS of overlap; every piece repeats the subsection heading.
  4. Every chunk starts with a context line: "<drug> | <label title> | <top-level heading>".
  5. Tag each chunk with the v1 drugs and genes it mentions (plus the label's own drug).
Token counts use the embedding model's own tokenizer, so no chunk is cut off when embedded.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.ingest.dailymed import LabelDocument, LabelSection
from drug_safety.rag.tagging import Tagger, default_tagger
from drug_safety.scope import load_scope

log = logging.getLogger(__name__)

CHUNKER_VERSION = "labels-v2"  # v2: drug-class tags
TARGET_TOKENS = 400  # pack small subsections up to about this size
MAX_TOKENS = 480  # hard limit per chunk; bge-base reads at most 512 including 2 special tokens
OVERLAP_TOKENS = 50  # overlap between pieces of one long subsection
SEPARATORS = ["\n\n", "\n", "; ", ". ", " ", ""]

# Section types that carry evidence for flags.
KEEP_SECTIONS = {
    "boxed_warning",
    "indications_and_usage",
    "dosage_and_administration",
    "contraindications",
    "warnings_and_precautions",
    "warnings",
    "precautions",
    "adverse_reactions",
    "drug_interactions",
    "use_in_specific_populations",
    "overdosage",
    "clinical_pharmacology",
    "drug_abuse_and_dependence",
    # OTC "Drug Facts" warning blocks
    "otc_do_not_use",
    "otc_ask_doctor",
    "otc_ask_doctor_pharmacist",
    "otc_stop_use",
    "otc_pregnancy_or_breast_feeding",
    "other_safety_information",
}
# Section types deliberately left out, and why.
DROP_SECTIONS = {
    "clinical_studies": "trial narratives; long, rarely the evidence for a flag",
    "spl_medguide": "patient leaflet; repeats the prescribing sections",
    "information_for_patients": "patient counselling; repeats the prescribing sections",
    "patient_counseling_information": "patient counselling; repeats the prescribing sections",
    "spl_patient_package_insert": "patient leaflet; repeats the prescribing sections",
    "instructions_for_use": "patient instructions",
    "nonclinical_toxicology": "animal and lab studies",
    "animal_pharmacology_and_or_toxicology": "animal studies",
    "microbiology": "not relevant to v1 flags",
    "description": "chemistry and inactive ingredients",
    "dosage_forms_and_strengths": "tablet strengths only",
    "how_supplied": "packaging",
    "storage_and_handling": "packaging",
    "inactive_ingredient": "excipients",
    "references": "reference list",
    "unclassified": "untitled boilerplate (distributor lines, revision dates)",
    "otc_active_ingredient": "ingredient line",
    "otc_purpose": "one-word purpose",
    "otc_questions": "phone number",
    "otc_keep_out_of_reach_of_children": "standard sentence",
}

TokenCounter = Callable[[str], int]


class Chunk(BaseModel):
    """Future kb.evidence_chunks row (embedding is added in the embedding step)."""

    chunk_id: str
    doc_id: str
    source: str
    tier: int
    section: str  # top-level section_key
    headings: list[list[str]]  # heading path of every subsection included
    text: str  # context line + body; this is what gets embedded and shown
    drugs: list[str]
    genes: list[str]
    # Classes the text names ("NSAIDs") and the v1 drugs in them. Kept apart from `drugs` so the
    # retriever's drug filter stays exact; classes are only a fallback.
    drug_classes: list[str] = []
    class_drugs: list[str] = []
    token_count: int
    source_version: str
    license: str
    chunker_version: str = CHUNKER_VERSION
    load_id: str
    embedding: list[float] | None = None
    embedding_model: str | None = None


class UnknownSectionError(ValueError):
    pass


# ---------------------------------------------------------------- tokenizer


def bge_token_counter(model_name: str) -> TokenCounter:
    """Count tokens exactly as the embedding model will (special tokens excluded)."""
    from tokenizers import Tokenizer

    tok = Tokenizer.from_pretrained(model_name)
    return lambda text: len(tok.encode(text, add_special_tokens=False).ids)


# ---------------------------------------------------------------- chunking


def _groups(sections: list[LabelSection]) -> list[list[LabelSection]]:
    """Consecutive records with the same top-level section."""
    groups: list[list[LabelSection]] = []
    for s in sections:
        if groups and groups[-1][0].section_key == s.section_key:
            groups[-1].append(s)
        else:
            groups.append([s])
    return groups


def _is_row(line: str) -> bool:
    return " | " in line or line.startswith("| ") or line.endswith(" |")


def _table_blocks(text: str) -> list[tuple[int, list[str]]]:
    """Paragraph blocks (separated by blank lines) that are tables: 2+ lines of 'a | b' rows."""
    blocks = text.split("\n\n")
    return [
        (i, b.split("\n"))
        for i, b in enumerate(blocks)
        if sum(_is_row(line) for line in b.split("\n")) >= 2
    ]


def split_long_tables(text: str, count: TokenCounter, budget: int) -> str:
    """Cut any table longer than `budget` into row groups, each repeating the table's caption
    and header row, so no chunk holds numbers without their column names. Groups are separated
    by blank lines, which the text splitter treats as safe cut points."""
    blocks = text.split("\n\n")
    for i, lines in _table_blocks(text):
        if count(blocks[i]) <= budget:
            continue
        first_row = next(j for j, line in enumerate(lines) if _is_row(line))
        caption = lines[:first_row]
        header = lines[first_row]
        rows = [line for line in lines[first_row + 1 :] if not line.startswith("Note ")]
        notes = [line for line in lines[first_row + 1 :] if line.startswith("Note ")]
        groups: list[list[str]] = []
        for row in rows:
            prefix = caption + ([f"{caption[-1]} (continued)"] if groups and caption else [])
            candidate = (groups[-1] if groups else []) + [row]
            if groups and count("\n".join(prefix + [header] + candidate)) <= budget:
                groups[-1].append(row)
            else:
                groups.append([row])
        out = []
        for g, group in enumerate(groups):
            title = caption if g == 0 else [f"{line} (continued)" for line in caption[-1:]]
            tail = notes if g == len(groups) - 1 else []
            out.append("\n".join(title + [header] + group + tail))
        blocks[i] = "\n\n".join(out)
    return "\n\n".join(blocks)


def _unit_text(s: LabelSection) -> str:
    """Subsection text preceded by its own heading (below the top-level one)."""
    heading = " > ".join(s.heading_path[1:])
    return f"{heading}\n{s.text}" if heading else s.text


def _context_line(doc: LabelDocument, group: list[LabelSection]) -> str:
    top = next((s.heading_path[0] for s in group if s.heading_path), None)
    top = top or group[0].section_key.replace("_", " ").upper()
    title = doc.title.split("[")[0].strip()
    return f"{doc.drugs[0]} | {title} | {top}"


def chunk_label(
    doc: LabelDocument,
    count: TokenCounter,
    tagger: Tagger,
    load_id: str,
    class_members: dict[str, list[str]] | None = None,
) -> list[Chunk]:
    class_members = class_members or {}
    unknown = {s.section_key for s in doc.sections} - KEEP_SECTIONS - set(DROP_SECTIONS)
    if unknown:
        raise UnknownSectionError(
            f"{doc.doc_id}: unclassified section types {sorted(unknown)}; "
            "add each to KEEP_SECTIONS or DROP_SECTIONS in rag/chunking.py"
        )

    chunks: list[Chunk] = []
    per_section: dict[str, int] = {}

    def emit(group_key: str, context: str, bodies: list[str], headings: list[list[str]]):
        text = context + "\n\n" + "\n\n".join(bodies)
        n = per_section.get(group_key, 0) + 1
        per_section[group_key] = n
        drugs = sorted(set(tagger.drugs(text)) | set(doc.drugs))
        classes = tagger.classes(text)
        chunks.append(
            Chunk(
                chunk_id=f"{doc.doc_id}:{group_key}:{n:03d}",
                doc_id=doc.doc_id,
                source=doc.source,
                tier=doc.tier,
                section=group_key,
                headings=headings,
                text=text,
                drugs=drugs,
                genes=tagger.genes(text),
                drug_classes=classes,
                class_drugs=sorted({d for c in classes for d in class_members.get(c, [])}),
                token_count=count(text),
                source_version=doc.source_version,
                license=doc.license,
                load_id=load_id,
            )
        )

    for group in _groups(doc.sections):
        key = group[0].section_key
        if key not in KEEP_SECTIONS:
            continue
        context = _context_line(doc, group)
        budget_target = TARGET_TOKENS - count(context) - 2
        budget_max = MAX_TOKENS - count(context) - 2

        bodies: list[str] = []
        headings: list[list[str]] = []
        size = 0
        for s in group:
            unit = _unit_text(s)
            unit_size = count(unit)
            if unit_size > budget_max:
                if bodies:
                    emit(key, context, bodies, headings)
                    bodies, headings, size = [], [], 0
                heading = " > ".join(s.heading_path[1:])
                head_cost = count(heading) + 1 if heading else 0
                splitter = RecursiveCharacterTextSplitter(
                    chunk_size=budget_max - head_cost,
                    chunk_overlap=OVERLAP_TOKENS,
                    length_function=count,
                    separators=SEPARATORS,
                    keep_separator="end",
                )
                prepared = split_long_tables(s.text, count, budget_max - head_cost)
                for piece in splitter.split_text(prepared):
                    body = f"{heading}\n{piece}" if heading else piece
                    emit(key, context, [body], [s.heading_path])
                continue
            if bodies and size + unit_size + 2 > budget_target:
                emit(key, context, bodies, headings)
                bodies, headings, size = [], [], 0
            bodies.append(unit)
            headings.append(s.heading_path)
            size += unit_size + 2
        if bodies:
            emit(key, context, bodies, headings)
    return chunks


# ---------------------------------------------------------------- run


def _paths(settings: Settings) -> tuple[Path, Path, Path]:
    labels = settings.data_dir / "processed" / "dailymed"
    out = settings.data_dir / "processed" / "chunks"
    return labels, out / "dailymed.jsonl", out / "dailymed_manifest.json"


def load_label_docs(settings: Settings) -> list[LabelDocument]:
    labels_dir, _, _ = _paths(settings)
    manifest = json.loads((labels_dir / "_manifest.json").read_text())
    return [
        LabelDocument.model_validate_json((labels_dir / f"{r['setid']}.json").read_text())
        for r in manifest
        if r["status"] == "ok"
    ]


def load_class_members(settings: Settings) -> dict[str, list[str]]:
    """class key -> v1 drugs in it, from the RxClass step (make fetch-classes)."""
    from drug_safety.ingest.rxclass import members, out_path

    if not out_path(settings).exists():
        raise FileNotFoundError("drug classes missing: run `make fetch-classes` first")
    return members(settings)


def run(settings: Settings, count: TokenCounter, tagger: Tagger) -> list[dict]:
    class_members = load_class_members(settings)
    load_id = f"chunks-dailymed-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    _, out_path, manifest_path = _paths(settings)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    manifest, all_chunks = [], []
    for doc in load_label_docs(settings):
        chunks = chunk_label(doc, count, tagger, load_id, class_members)
        all_chunks += chunks
        kept = {s.section_key for s in doc.sections} & KEEP_SECTIONS
        dropped = sorted({s.section_key for s in doc.sections} - KEEP_SECTIONS)
        tokens = [c.token_count for c in chunks]
        manifest.append(
            {
                "drug": doc.drugs[0],
                "doc_id": doc.doc_id,
                "chunks": len(chunks),
                "min_tokens": min(tokens),
                "max_tokens": max(tokens),
                "mean_tokens": round(sum(tokens) / len(tokens)),
                "sections_kept": sorted(kept),
                "sections_dropped": dropped,
                "genes_tagged": sorted({g for c in chunks for g in c.genes}),
                "load_id": load_id,
            }
        )
    tmp = out_path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for c in all_chunks:
            f.write(c.model_dump_json() + "\n")
    tmp.replace(out_path)  # never leave a half-written file behind
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def read_chunks(settings: Settings) -> list[Chunk]:
    _, out_path, _ = _paths(settings)
    with open(out_path, encoding="utf-8") as f:
        return [Chunk.model_validate_json(line) for line in f if line.strip()]


# ---------------------------------------------------------------- verify


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


# Domain spot checks: evidence the spec's examples depend on must land in a chunk that is
# tagged correctly. (drug, gene or None, required text, other drug that must be tagged or None)
GOLD = [
    ("clopidogrel", "CYP2C19", "poor metabolizer", None),
    ("clopidogrel", None, "omeprazole", "omeprazole"),
    ("warfarin", "VKORC1", "VKORC1", None),
    ("warfarin", "CYP2C9", "CYP2C9", None),
    ("codeine", "CYP2D6", "ultra-rapid", None),
    ("simvastatin", "SLCO1B1", "OATP1B1", None),
    ("metformin", None, "eGFR", None),
    ("clozapine", None, "smok", None),
    ("theophylline", None, "smok", None),
    ("atorvastatin", None, "grapefruit", None),
    ("warfarin", None, "vitamin K", None),
]


# Class spot checks: (label drug, class named in its text, v1 drug that class must reach).
# The member must come only through the class, not be named in the chunk itself.
GOLD_CLASSES = [
    ("clopidogrel", "nsaid", "ibuprofen"),  # Plavix 7.4 "Nonsteroidal Anti-inflammatory Drugs"
    ("clopidogrel", "ssri", "sertraline"),  # Plavix 7.6 "SSRIs and SNRIs"
    ("clopidogrel", "opioid", "codeine"),  # Plavix 7.3 "Opioids"
]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;:])\s+|\n+", text)
    return [" ".join(p.split()) for p in parts if len(p.strip()) >= 3]


def verify(
    settings: Settings,
    count: TokenCounter,
    tagger: Tagger,
    gold: list[tuple] = GOLD,
    gold_classes: list[tuple] | None = None,
) -> list[Check]:
    gold_classes = GOLD_CLASSES if gold_classes is None else gold_classes
    checks: list[Check] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append(Check(name=name, ok=ok, detail="" if ok else detail))

    _, out_path, _ = _paths(settings)
    if not out_path.exists():
        add("chunks file exists", False, "run `make chunk-labels`")
        return checks
    chunks = read_chunks(settings)
    docs = load_label_docs(settings)
    by_doc: dict[str, list[Chunk]] = {}
    for c in chunks:
        by_doc.setdefault(c.doc_id, []).append(c)
    scope = load_scope()

    add(
        "every loaded label has chunks",
        all(d.doc_id in by_doc for d in docs),
        str([d.drugs[0] for d in docs if d.doc_id not in by_doc]),
    )
    add(
        "no chunks from unknown labels",
        set(by_doc) <= {d.doc_id for d in docs},
        str(set(by_doc) - {d.doc_id for d in docs}),
    )

    ids = [c.chunk_id for c in chunks]
    add("chunk_ids unique", len(ids) == len(set(ids)), "duplicates found")
    bad_ids = [
        c.chunk_id
        for c in chunks
        if not re.fullmatch(rf"{re.escape(c.doc_id)}:{c.section}:\d{{3}}", c.chunk_id)
    ]
    add("chunk_id format {doc_id}:{section}:NNN", not bad_ids, str(bad_ids[:3]))

    too_long = [(c.chunk_id, n) for c in chunks if (n := count(c.text)) + 2 > 512]
    add("fits the embedding model (<= 512 tokens)", not too_long, str(too_long[:3]))
    over = [c.chunk_id for c in chunks if c.token_count > MAX_TOKENS]
    add(f"within chunk limit ({MAX_TOKENS} tokens)", not over, str(over[:3]))
    stale = [c.chunk_id for c in chunks if c.token_count != count(c.text)]
    add("stored token counts are correct", not stale, str(stale[:3]))

    dropped = [c.chunk_id for c in chunks if c.section not in KEEP_SECTIONS]
    add("only kept sections chunked", not dropped, str(dropped[:3]))

    own = {d.doc_id: d.drugs[0] for d in docs}
    no_own = [c.chunk_id for c in chunks if own.get(c.doc_id) not in c.drugs]
    add("every chunk tagged with its label's drug", not no_own, str(no_own[:3]))
    bad_tags = [
        c.chunk_id
        for c in chunks
        if not set(c.drugs) <= set(scope.drugs) or not set(c.genes) <= set(scope.genes)
    ]
    add("tags are v1 drugs and genes only", not bad_tags, str(bad_tags[:3]))
    class_members = load_class_members(settings)
    retag = [
        c.chunk_id
        for c in chunks
        if sorted(set(tagger.drugs(c.text)) | {own[c.doc_id]}) != c.drugs
        or tagger.genes(c.text) != c.genes
        or tagger.classes(c.text) != c.drug_classes
        or sorted({d for k in c.drug_classes for d in class_members.get(k, [])}) != c.class_drugs
    ]
    add(
        "tags match the current dictionaries (drugs, genes, classes)",
        not retag,
        f"{len(retag)} chunks; dictionary changed since chunking, rerun",
    )

    # Nothing lost: every sentence of every kept section appears in that label's chunks.
    missing = []
    for d in docs:
        body = " ".join(" ".join(c.text.split()) for c in by_doc.get(d.doc_id, []))
        for s in d.sections:
            if s.section_key in KEEP_SECTIONS:
                missing += [(d.drugs[0], x[:60]) for x in _sentences(s.text) if x not in body]
    add(
        "no text lost from kept sections",
        not missing,
        f"{len(missing)} missing, e.g. {missing[:2]}",
    )

    # Tables: a chunk holding rows of a table must also hold that table's header row.
    # (The same row text can appear in several tables, so a row is only orphaned if none of
    # the tables it belongs to has its header in the chunk.)
    orphans = []
    for d in docs:
        headers_of: dict[str, set[str]] = {}
        for s in d.sections:
            if s.section_key not in KEEP_SECTIONS:
                continue
            for _, lines in _table_blocks(s.text):
                rows = [line for line in lines if _is_row(line)]
                for r in rows[1:]:
                    if len(r) >= 10:
                        headers_of.setdefault(r, set()).add(rows[0])
        for c in by_doc.get(d.doc_id, []):
            lines = set(c.text.split("\n"))
            orphans += [
                (c.chunk_id, r[:40]) for r in lines & set(headers_of) if not headers_of[r] & lines
            ]
    add(
        "table rows always come with their header row",
        not orphans,
        f"{len(orphans)} rows, e.g. {orphans[:2]}",
    )

    # Reproducible: chunking the same labels again gives the same chunks.
    again = [c for d in docs for c in chunk_label(d, count, tagger, "x", class_members)]
    key = lambda c: (c.chunk_id, c.text, c.drugs, c.genes, c.drug_classes, c.class_drugs)  # noqa: E731
    same = [key(c) for c in again] == [key(c) for c in chunks]
    add(
        "up to date (re-chunking gives identical output)",
        same,
        "labels or chunker changed since the last run; run `make chunk-labels`",
    )

    for drug, gene, needle, other in gold:
        hit = [
            c
            for c in chunks
            if drug in c.drugs
            and needle.lower() in c.text.lower()
            and c.doc_id in own
            and own[c.doc_id] == drug
            and (gene is None or gene in c.genes)
            and (other is None or other in c.drugs)
        ]
        what = (
            f"{drug}: '{needle}'"
            + (f" tagged {gene}" if gene else "")
            + (f" tagged {other}" if other else "")
        )
        add(f"gold: {what}", bool(hit), "no matching chunk")

    for drug, cls, member in gold_classes:
        hit = [
            c
            for c in chunks
            if own.get(c.doc_id) == drug
            and cls in c.drug_classes
            and member in c.class_drugs
            and member not in c.drugs
        ]
        add(
            f"gold class: {drug} label mentions {cls}, which covers {member}",
            bool(hit),
            "no matching chunk",
        )
    return checks


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m drug_safety.rag.chunking")
    p.add_argument("cmd", choices=["run", "verify"])
    args = p.parse_args(argv)
    count = bge_token_counter(settings.embedding_model)
    tagger = default_tagger()

    if args.cmd == "run":
        manifest = run(settings, count, tagger)
        total = sum(m["chunks"] for m in manifest)
        print(f"{'drug':14} {'chunks':>6} {'min':>4} {'mean':>4} {'max':>4}  genes")
        for m in manifest:
            print(
                f"{m['drug']:14} {m['chunks']:6} {m['min_tokens']:4} {m['mean_tokens']:4} "
                f"{m['max_tokens']:4}  {', '.join(m['genes_tagged'])}"
            )
        print(f"\n{total} chunks from {len(manifest)} labels.")
        return 0

    checks = verify(settings, count, tagger)
    failed = [c for c in checks if not c.ok]
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name}" + (f": {c.detail}" if c.detail else ""))
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed.")
    print("RESULT:", "PASS" if not failed else "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
