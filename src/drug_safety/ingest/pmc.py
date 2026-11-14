"""PMC full text for the selected papers, through the BioC API (an approved PMC service).

Usage:
    python -m drug_safety.ingest.pmc fetch     # network, resumable; ~1 request/second
    python -m drug_safety.ingest.pmc parse     # offline: licence decision + sections -> JSON
    python -m drug_safety.ingest.pmc verify    # offline checks

Licence policy (spec: "Full text is kept only where the paper's licence allows reuse"):
  - full text allowed: CC0, CC BY, CC BY-SA, CC BY-NC, CC BY-NC-SA (NC = non-commercial only;
    recorded per paper so it can be removed if the project becomes commercial);
  - metadata only: No-Derivatives licences (CC BY-ND, CC BY-NC-ND; chunking is a derivative),
    "NO-CC CODE" (in the open-access set without a Creative Commons licence; terms vary by
    publisher), and anything unrecognised.

Kept sections: title, abstract, results, discussion, conclusions, tables, and their captions.
Dropped: introduction, methods, references, figures, acknowledgements, funding, conflicts,
author contributions, abbreviations, supplementary material.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.ingest.dailymed import USER_AGENT
from drug_safety.ingest.pubmed_select import read as read_selection

log = logging.getLogger(__name__)

BIOC = "https://www.ncbi.nlm.nih.gov/research/bionlp/RESTful/pmcoa.cgi/BioC_json/{}/unicode"
SOURCE = "pmc"
FULL_TEXT_LICENSES = {
    "CC0": "CC0",
    "CC BY": "CC BY",
    "CC BY-SA": "CC BY-SA",
    "CC BY-NC": "CC BY-NC",
    "CC BY-NC-SA": "CC BY-NC-SA",
}
KEEP_SECTIONS = {"TITLE", "ABSTRACT", "RESULTS", "DISCUSS", "CONCL", "TABLE"}
DROP_TYPES = {"ref", "fig_caption", "fig_title_caption", "footnote", "front"}


def normalise_license(raw: str | None) -> str:
    """'cc by-nc 4.0' -> 'CC BY-NC'; unknown -> the raw string upper-cased."""
    s = " ".join((raw or "").upper().replace("_", " ").split())
    s = re.sub(r"\s+\d+(\.\d+)?$", "", s)  # drop a version after a space ("4.0"), keep "CC0"
    s = re.sub(r"^CC-", "CC ", s)
    s = s.replace("CC-BY", "CC BY")
    return s or "UNKNOWN"


def full_text_allowed(license_: str) -> bool:
    return license_ in FULL_TEXT_LICENSES


class PaperSection(BaseModel):
    section: str  # BioC section_type, e.g. RESULTS
    kind: str  # BioC passage type, e.g. paragraph, table, table_caption
    heading: str  # nearest preceding title passage, e.g. "Results"
    text: str


class PaperDocument(BaseModel):
    doc_id: str
    source: str = SOURCE
    external_id: str  # PMCID
    pmid: str
    doi: str | None
    title: str
    journal: str
    pub_year: int | None
    drugs: list[str]
    genes: list[str]
    study_type: str
    tier: int
    license: str
    full_text: bool
    license_note: str
    raw_path: str
    retrieved_at: str
    load_id: str
    sections: list[PaperSection]


# ---------------------------------------------------------------- fetch


def raw_path(settings: Settings, pmcid: str) -> Path:
    return settings.raw_dir / SOURCE / f"{pmcid}.json"


def is_bioc(content: bytes) -> bool:
    """A real BioC JSON response: a list holding a collection with one document."""
    try:
        body = json.loads(content)
        return bool(body[0]["documents"][0]["passages"])
    except (ValueError, KeyError, IndexError, TypeError):
        return False


def fetch(settings: Settings, delay: float = 1.0) -> Counter:
    """Fetch BioC JSON for every selected paper that isn't on disk yet (resumable)."""
    http = httpx.Client(timeout=60, headers={"User-Agent": USER_AGENT})
    status: Counter = Counter()
    papers = [p for p in read_selection(settings) if p.selected]
    for i, p in enumerate(papers, 1):
        path = raw_path(settings, p.pmcid)
        if path.exists():
            status["already on disk"] += 1
            continue
        for attempt in range(6):
            time.sleep(delay)
            try:
                r = http.get(BIOC.format(p.pmcid))
            except httpx.TransportError as e:
                log.warning("network error %s", e)
                continue
            if r.status_code == 200 and r.content.startswith(b"[Error]"):
                # BioC answers HTTP 200 with an error page for papers it hasn't processed yet
                # (seen for very recent PMCIDs). Not saved; the paper stays metadata-only.
                status["not available in BioC (error page)"] += 1
                break
            if r.status_code == 200 and is_bioc(r.content):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(r.content)
                status["fetched"] += 1
                break
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(60, 5 * 2**attempt))  # back off; NCBI asks for patience
                continue
            status[f"HTTP {r.status_code} (not available in BioC)"] += 1
            break
        else:
            status["gave up after retries"] += 1
        if i % 25 == 0:
            log.warning("%d/%d %s", i, len(papers), dict(status))
    return status


# ---------------------------------------------------------------- parse


def parse_bioc(body: list) -> tuple[str, list[PaperSection]]:
    doc = body[0]["documents"][0]
    lic = normalise_license(doc.get("infons", {}).get("license"))
    sections, heading = [], ""
    for p in doc["passages"]:
        sec = p["infons"].get("section_type", "")
        kind = p["infons"].get("type", "")
        text = " ".join(p.get("text", "").split())
        if not text:
            continue
        if kind.startswith("title") or kind.endswith("title_1") or kind == "abstract_title_1":
            heading = text
            continue
        if sec not in KEEP_SECTIONS or kind in DROP_TYPES:
            continue
        if re.fullmatch(r"(?i)\s*(references?|bibliography|literature cited)\s*", heading):
            continue  # some papers put the reference list in a "table" (seen in the first load)
        sections.append(
            PaperSection(section=sec, kind=kind, heading=heading or sec.title(), text=text)
        )
    return lic, sections


def out_dir(settings: Settings) -> Path:
    return settings.data_dir / "processed" / "papers" / "documents"


def parse(settings: Settings) -> dict:
    load_id = f"{SOURCE}-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    od = out_dir(settings)
    od.mkdir(parents=True, exist_ok=True)
    manifest = []
    for p in [p for p in read_selection(settings) if p.selected]:
        path = raw_path(settings, p.pmcid)
        row = {"pmcid": p.pmcid, "pmid": p.pmid, "tier": p.tier}
        if not path.exists():
            manifest.append(row | {"status": "not fetched"})
            continue
        lic, sections = parse_bioc(json.loads(path.read_bytes()))
        allowed = full_text_allowed(lic)
        doc = PaperDocument(
            doc_id=f"{SOURCE}:{p.pmcid}",
            external_id=p.pmcid,
            pmid=p.pmid,
            doi=p.doi,
            title=p.title,
            journal=p.journal,
            pub_year=p.pub_year,
            drugs=p.drugs,
            genes=p.genes,
            study_type=p.study_type,
            tier=p.tier,
            license=lic,
            full_text=allowed,
            license_note=(
                "full text reuse allowed" + (" (non-commercial only)" if "NC" in lic else "")
            )
            if allowed
            else "metadata only: licence doesn't clearly allow reuse of the full text",
            raw_path=str(path),
            retrieved_at=datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(
                timespec="seconds"
            ),
            load_id=load_id,
            sections=sections if allowed else [],
        )
        (od / f"{p.pmcid}.json").write_text(doc.model_dump_json(indent=1), encoding="utf-8")
        manifest.append(
            row
            | {
                "status": "ok",
                "license": lic,
                "full_text": allowed,
                "sections": len(doc.sections),
                "chars": sum(len(s.text) for s in doc.sections),
            }
        )
    (od / "_manifest.json").write_text(json.dumps(manifest, indent=1))
    ok = [m for m in manifest if m["status"] == "ok"]
    return {
        "selected": len(manifest),
        "fetched": len(ok),
        "not_fetched": len(manifest) - len(ok),
        "licenses": dict(Counter(m["license"] for m in ok).most_common()),
        "full_text": sum(m["full_text"] for m in ok),
        "metadata_only": sum(not m["full_text"] for m in ok),
        "full_text_by_tier": dict(sorted(Counter(m["tier"] for m in ok if m["full_text"]).items())),
    }


# ---------------------------------------------------------------- verify


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


def verify(settings: Settings) -> list[Check]:
    checks: list[Check] = []

    def add(name, ok, detail=""):
        checks.append(Check(name=name, ok=bool(ok), detail="" if ok else str(detail)))

    od = out_dir(settings)
    manifest = json.loads((od / "_manifest.json").read_text())
    sel = {p.pmcid for p in read_selection(settings) if p.selected}
    add("one manifest row per selected paper", {m["pmcid"] for m in manifest} == sel, "differs")
    missing = [m["pmcid"] for m in manifest if m["status"] != "ok"]
    add("report: selected papers BioC couldn't provide (not a failure)", True)
    checks[-1].detail = f"{len(missing)}: {missing[:10]}"
    bad = []
    for m in manifest:
        if m["status"] != "ok":
            continue
        d = PaperDocument.model_validate_json((od / f"{m['pmcid']}.json").read_text())
        lic, sections = parse_bioc(json.loads(Path(d.raw_path).read_bytes()))
        if d.license != lic or d.full_text != full_text_allowed(lic):
            bad.append((d.external_id, "licence decision differs from the raw file"))
        if not d.full_text and d.sections:
            bad.append((d.external_id, "full text stored although the licence doesn't allow it"))
        if d.full_text and d.sections != sections:
            bad.append((d.external_id, "sections differ from re-parsing the raw file"))
        if any(s.section not in KEEP_SECTIONS for s in d.sections):
            bad.append((d.external_id, "a dropped section type is present"))
        if d.full_text and not any(s.section == "ABSTRACT" for s in d.sections):
            bad.append((d.external_id, "full-text paper with no abstract"))
    add("licence decision, stored text and sections match the raw files", not bad, bad[:5])
    return checks


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    logging.basicConfig(level="WARNING", format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m drug_safety.ingest.pmc")
    p.add_argument("cmd", choices=["fetch", "parse", "verify"])
    args = p.parse_args(argv)
    if args.cmd == "fetch":
        print(json.dumps(dict(fetch(settings)), indent=2))
        return 0
    if args.cmd == "parse":
        print(json.dumps(parse(settings), indent=2))
        return 0
    checks = verify(settings)
    failed = [c for c in checks if not c.ok]
    for c in checks:
        print(f"{'PASS' if c.ok else 'FAIL'}  {c.name}" + (f": {c.detail}" if c.detail else ""))
    print("RESULT:", "PASS" if not failed else "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
