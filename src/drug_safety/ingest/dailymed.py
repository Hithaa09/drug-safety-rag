"""DailyMed loader: fetch the pinned FDA label for each v1 drug, keep the raw XML, split it into
sections.

Usage:
    python -m drug_safety.ingest.dailymed load                 # all pinned drugs
    python -m drug_safety.ingest.dailymed load --drug warfarin  # one drug
    python -m drug_safety.ingest.dailymed suggest --drug warfarin

Writes:
    data/raw/dailymed/<setid>/v<version>.xml      untouched SPL XML (never edited)
    data/processed/dailymed/<setid>.json          document metadata + sections
    data/processed/dailymed/_manifest.json        one line per drug: what was loaded

Database loading into kb.documents happens later, once the table schema is agreed with Person A.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from lxml import etree
from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.scope import load_scope

log = logging.getLogger(__name__)

BASE_URL = "https://dailymed.nlm.nih.gov/dailymed/services/v2"
USER_AGENT = "drug-safety-rag/0.1 (research prototype)"
NS = {"v": "urn:hl7-org:v3"}
SOURCE = "dailymed"
LICENSE = "DailyMed (NLM) SPL; see DailyMed terms of service"
PINS_FILE = Path("scope/dailymed_labels.yaml")

RX_DOCTYPE = "34391-3"  # HUMAN PRESCRIPTION DRUG LABEL
OTC_DOCTYPE = "34390-5"  # HUMAN OTC DRUG LABEL
NDA, ANDA = "C73594", "C73584"  # marketing category codes

# Sections with no clinical text: product data tables and package-label images.
SKIP_SECTION_CODES = {"48780-1", "51945-4"}
UNCLASSIFIED = "42229-5"


# ---------------------------------------------------------------- models


class LabelSection(BaseModel):
    """One section or subsection with its own text (child subsections are separate records)."""

    section_key: str  # top-level section, e.g. "drug_interactions"
    section_code: str  # top-level LOINC code, e.g. "34073-7"
    code: str  # this (sub)section's LOINC code
    heading_path: list[str]  # e.g. ["12 CLINICAL PHARMACOLOGY", "12.5 Pharmacogenomics"]
    text: str


class LabelDocument(BaseModel):
    """A parsed label: the future kb.documents row plus its sections."""

    doc_id: str
    source: str = SOURCE
    external_id: str  # set ID
    title: str
    labeler: str | None
    doc_type_code: str
    doc_type: str
    source_version: str  # SPL version number
    effective_date: str  # YYYY-MM-DD
    pub_year: int | None
    drugs: list[str]
    genes: list[str] = []  # tagged later by the rag module
    study_type: str = "label"
    tier: int = 1
    license: str = LICENSE
    raw_path: str
    retrieved_at: str
    load_id: str
    sections: list[LabelSection]


class Pin(BaseModel):
    setid: str | None
    title: str | None = None
    kind: str
    review: str
    query: str | None = None
    note: str | None = None


# ---------------------------------------------------------------- parsing


def _local(tag: object) -> str:
    return tag.split("}", 1)[1] if isinstance(tag, str) and "}" in tag else str(tag)


def _clean(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out: list[str] = []
    for line in lines:
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def _render(el: etree._Element, notes: list[str] | None = None) -> str:
    """Turn an SPL <text> subtree into plain text: paragraphs and list items on their own
    lines, table cells joined with ' | ', images dropped.

    Inside a table, footnotes become a "[note N]" marker in the cell and are listed as
    "Note N: ..." lines below the table, so header cells stay readable."""
    if not isinstance(el.tag, str):  # comments, processing instructions
        return ""
    tag = _local(el.tag)
    if tag in ("renderMultiMedia", "observationMedia"):
        return ""
    if tag == "br":
        return "\n"
    if tag == "footnote" and notes is not None:
        notes.append(" ".join(_render(el).split()))
        return f" [note {len(notes)}]"
    if tag == "table":
        rows, table_notes = [], []
        caption = el.find("v:caption", NS)
        if caption is not None:
            rows.append(_clean(_render(caption)))
        for tr in el.iter(f"{{{NS['v']}}}tr"):
            cells = [
                " ".join(_render(c, table_notes).split())
                for c in tr
                if isinstance(c.tag, str) and _local(c.tag) in ("td", "th")
            ]
            if any(cells):
                rows.append(" | ".join(cells))
        rows += [f"Note {i}: {text}" for i, text in enumerate(table_notes, 1)]
        return "\n" + "\n".join(rows) + "\n"

    inner = el.text or ""
    for child in el:
        inner += _render(child, notes) + (child.tail or "")
    if tag == "item":
        return "\n- " + inner.strip() + "\n"
    if tag in ("paragraph", "caption", "footnote", "list"):
        return "\n" + inner + "\n"
    return inner


def _title(sec: etree._Element) -> str:
    t = sec.find("v:title", NS)
    return " ".join("".join(t.itertext()).split()) if t is not None else ""


def _slug(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"\bsection\b", "", text)
    text = re.sub(r"^[\d.\s]+", "", text)  # leading numbering like "8.6 "
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_") or "unclassified"


def _section_key(sec: etree._Element) -> str:
    code = sec.find("v:code", NS)
    code_value = code.get("code", "") if code is not None else ""
    display = code.get("displayName", "") if code is not None else ""
    if code_value == UNCLASSIFIED or not display:
        return _slug(_title(sec)) if _title(sec) else "unclassified"
    return _slug(display)


def _walk(sec, key, top_code, path, out):
    code_el = sec.find("v:code", NS)
    code = code_el.get("code", "") if code_el is not None else ""
    title = _title(sec)
    if not title and code != UNCLASSIFIED and code_el is not None:
        # OTC subsections ("Do not use", "Ask a doctor before use") have a code but no title.
        display = re.sub(r"\s*SECTION$", "", code_el.get("displayName", "").strip())
        title = display.capitalize() if path and display else ""
    heading_path = path + ([title] if title else [])
    text_el = sec.find("v:text", NS)  # direct text only; highlights (<excerpt>) are skipped
    text = _clean(_render(text_el)) if text_el is not None else ""
    if text and out and out[-1].section_key == key and out[-1].heading_path == heading_path:
        # Untitled sub-subsection (e.g. a "Bleeding" or "Data" block): its heading is a paragraph
        # inside the text, so keep it with the preceding text under the same heading.
        out[-1].text += "\n\n" + text
    elif text:
        out.append(
            LabelSection(
                section_key=key,
                section_code=top_code,
                code=code,
                heading_path=heading_path,
                text=text,
            )
        )
    for child in sec.findall("v:component/v:section", NS):
        _walk(child, key, top_code, heading_path, out)


def parse_spl(xml: bytes) -> dict:
    """Parse SPL XML into metadata and a flat list of sections (in document order)."""
    root = etree.fromstring(xml)
    doc_code = root.find("v:code", NS)
    eff = root.find("v:effectiveTime", NS).get("value", "")
    labeler = root.find("v:author/v:assignedEntity/v:representedOrganization/v:name", NS)
    sections: list[LabelSection] = []
    for sec in root.findall("v:component/v:structuredBody/v:component/v:section", NS):
        code_el = sec.find("v:code", NS)
        code = code_el.get("code", "") if code_el is not None else ""
        if code in SKIP_SECTION_CODES:
            continue
        _walk(sec, _section_key(sec), code, [], sections)
    return {
        "setid": root.find("v:setId", NS).get("root"),
        "version": root.find("v:versionNumber", NS).get("value"),
        "effective_date": f"{eff[:4]}-{eff[4:6]}-{eff[6:8]}" if len(eff) >= 8 else "",
        "doc_type_code": doc_code.get("code", ""),
        "doc_type": doc_code.get("displayName", ""),
        "labeler": " ".join(labeler.text.split()) if labeler is not None and labeler.text else None,
        "sections": sections,
    }


# ---------------------------------------------------------------- label choice (suggest)


def is_single_ingredient_oral(title: str, query: str) -> bool:
    """True for e.g. 'PLAVIX (CLOPIDOGREL) TABLET', false for combinations, injections, kits."""
    head = title.split("[")[0].upper()
    m = re.search(r"\(([^)]*)\)", head)
    # Without brackets the ingredients are the words before the dosage form; the form itself
    # may contain commas ("CAPSULE, DELAYED RELEASE"), which must not count as a combination.
    ingredients = m.group(1) if m else re.split(r"\b(?:TABLET|CAPSULE)\b", head)[0]
    return (
        query.upper() in head
        and not re.search(r"\bAND\b|,|/", ingredients)
        and re.search(r"\b(TABLET|CAPSULE)\b", head) is not None
        and "KIT" not in head
    )


# ---------------------------------------------------------------- HTTP


class DailyMedClient:
    """Thin client with polite pacing and retries. Responses are not cached here because raw
    XML is already kept on disk by the loader."""

    def __init__(self, base_url: str = BASE_URL, delay: float = 0.25, timeout: float = 60):
        self.base_url = base_url
        self.delay = delay
        self.http = httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        for attempt in range(4):
            time.sleep(self.delay)
            try:
                r = self.http.get(f"{self.base_url}{path}", params=params)
            except httpx.TransportError as e:
                log.warning("network error %s (attempt %d)", e, attempt + 1)
            else:
                if r.status_code == 200:
                    return r
                if r.status_code not in (429, 500, 502, 503, 504):
                    r.raise_for_status()
                log.warning("HTTP %d for %s (attempt %d)", r.status_code, path, attempt + 1)
            time.sleep(2**attempt)
        raise RuntimeError(f"DailyMed request failed after retries: {path}")

    def spl_xml(self, setid: str) -> bytes:
        return self._get(f"/spls/{setid}.xml").content

    def history(self, setid: str) -> list[dict]:
        """All versions of a label, newest first: [{spl_version, published_date}, ...]."""
        return self._get(f"/spls/{setid}/history.json").json()["data"]["history"]

    def search(self, drug_name: str, doctype: str, category: str | None = None) -> list[dict]:
        params = {"drug_name": drug_name, "doctype": doctype, "pagesize": 100, "page": 1}
        if category:
            params["marketing_category_code"] = category
        results: list[dict] = []
        while True:
            body = self._get("/spls.json", params).json()
            results += body["data"]
            if params["page"] >= body["metadata"]["total_pages"] or params["page"] >= 3:
                return results
            params["page"] += 1


def suggest(client: DailyMedClient, query: str) -> tuple[str, dict] | None:
    """Apply the selection rule: recent brand (<= 3 years), else newest generic, else old brand,
    else OTC. Results come back newest first."""

    def oral(rows):
        return [r for r in rows if is_single_ingredient_oral(r["title"], query)]

    def age_years(r):
        published = datetime.strptime(r["published_date"], "%b %d, %Y")
        return (datetime.now() - published).days / 365

    brand = oral(client.search(query, RX_DOCTYPE, NDA))
    if brand and age_years(brand[0]) <= 3:
        return "brand", brand[0]
    generic = oral(client.search(query, RX_DOCTYPE, ANDA))
    if generic:
        return "generic", generic[0]
    if brand:
        return "brand", brand[0]
    otc = oral(client.search(query, OTC_DOCTYPE))
    return ("otc", otc[0]) if otc else None


# ---------------------------------------------------------------- loading


def load_pins(path: Path = PINS_FILE) -> dict[str, Pin]:
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)["labels"]
    return {drug: Pin.model_validate(v) for drug, v in raw.items()}


def load_label(
    drug: str, pin: Pin, client: DailyMedClient, settings: Settings, load_id: str
) -> LabelDocument:
    xml = client.spl_xml(pin.setid)
    parsed = parse_spl(xml)
    if parsed["setid"] != pin.setid:
        raise ValueError(f"{drug}: asked for {pin.setid}, got {parsed['setid']}")

    raw_path = settings.raw_dir / SOURCE / pin.setid / f"v{parsed['version']}.xml"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if not raw_path.exists():  # a version is immutable; never overwrite it
        raw_path.write_bytes(xml)

    doc = LabelDocument(
        doc_id=f"{SOURCE}:{pin.setid}",
        external_id=pin.setid,
        title=pin.title or parsed["doc_type"],
        labeler=parsed["labeler"],
        doc_type_code=parsed["doc_type_code"],
        doc_type=parsed["doc_type"],
        source_version=parsed["version"],
        effective_date=parsed["effective_date"],
        pub_year=int(parsed["effective_date"][:4]) if parsed["effective_date"] else None,
        drugs=[drug],
        raw_path=str(raw_path),
        retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"),
        load_id=load_id,
        sections=parsed["sections"],
    )
    out = settings.data_dir / "processed" / SOURCE / f"{pin.setid}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc.model_dump_json(indent=2), encoding="utf-8")
    return doc


def run_load(drugs: list[str], client: DailyMedClient, settings: Settings) -> list[dict]:
    pins = load_pins()
    load_id = f"{SOURCE}-{datetime.now(UTC):%Y%m%dT%H%M%S}"
    manifest = []
    for drug in drugs:
        pin = pins.get(drug)
        row = {"drug": drug, "load_id": load_id}
        if pin is None:
            row |= {"status": "error", "detail": "no entry in scope/dailymed_labels.yaml"}
        elif pin.setid is None:
            row |= {"status": "missing", "detail": pin.note}
        else:
            try:
                doc = load_label(drug, pin, client, settings, load_id)
                # Old-format labels keep these under PRECAUTIONS or WARNINGS, so check headings.
                keys = {s.section_key for s in doc.sections} | {
                    _slug(h) for s in doc.sections for h in s.heading_path
                }
                row |= {
                    "status": "ok",
                    "setid": pin.setid,
                    "version": doc.source_version,
                    "doc_type": doc.doc_type,
                    "review": pin.review,
                    "sections": len(doc.sections),
                    "chars": sum(len(s.text) for s in doc.sections),
                    "has_drug_interactions": "drug_interactions" in keys,
                    "has_contraindications": "contraindications" in keys,
                }
            except Exception as e:  # keep going; the manifest records the failure
                log.exception("failed: %s", drug)
                row |= {"status": "error", "detail": str(e)}
        log.info("%-14s %s", drug, row["status"])
        manifest.append(row)

    # Update only the drugs loaded in this run; keep the other drugs' rows.
    path = settings.data_dir / "processed" / SOURCE / "_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = {r["drug"]: r for r in json.loads(path.read_text())} if path.exists() else {}
    rows |= {r["drug"]: r for r in manifest}
    order = {d: i for i, d in enumerate(load_scope().drugs)}
    merged = sorted(rows.values(), key=lambda r: order.get(r["drug"], len(order)))
    path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return manifest


# ---------------------------------------------------------------- update check


def check_updates(client: DailyMedClient, settings: Settings) -> list[dict]:
    """Compare each loaded label's version with the newest version on DailyMed."""
    path = settings.data_dir / "processed" / SOURCE / "_manifest.json"
    rows = []
    for r in json.loads(path.read_text()):
        if r["status"] != "ok":
            continue
        newest = max(client.history(r["setid"]), key=lambda h: int(h["spl_version"]))
        rows.append(
            {
                "drug": r["drug"],
                "loaded_version": int(r["version"]),
                "newest_version": int(newest["spl_version"]),
                "newest_published": newest["published_date"],
                "update_available": int(newest["spl_version"]) > int(r["version"]),
            }
        )
    return rows


# ---------------------------------------------------------------- verify


class Check(BaseModel):
    drug: str
    name: str
    ok: bool
    detail: str = ""


def verify(settings: Settings) -> list[Check]:
    """Offline acceptance checks on what `load` wrote. No network. Every check must pass."""
    checks: list[Check] = []

    def add(drug: str, name: str, ok: bool, detail: str = "") -> bool:
        checks.append(Check(drug=drug, name=name, ok=ok, detail="" if ok else detail))
        return ok

    pins = load_pins()
    scope_drugs = load_scope().drugs
    add(
        "*",
        "every scope drug has a pin",
        set(pins) == set(scope_drugs),
        f"differs: {sorted(set(pins) ^ set(scope_drugs))}",
    )

    out_dir = settings.data_dir / "processed" / SOURCE
    manifest_path = out_dir / "_manifest.json"
    if not add("*", "manifest exists", manifest_path.exists(), "run `make ingest-dailymed`"):
        return checks
    manifest = {r["drug"]: r for r in json.loads(manifest_path.read_text())}

    for drug in scope_drugs:
        pin, row = pins.get(drug), manifest.get(drug)
        if not add(drug, "in manifest", row is not None, "not loaded yet"):
            continue
        if pin is None or pin.setid is None:
            add(drug, "recorded as missing", row["status"] == "missing", f"status={row['status']}")
            continue
        if not add(drug, "loaded ok", row["status"] == "ok", row.get("detail", "")):
            continue
        add(
            drug,
            "manifest matches pin",
            row.get("setid") == pin.setid,
            "pin changed since last load; rerun load",
        )

        doc_path = out_dir / f"{pin.setid}.json"
        if not add(drug, "processed file exists", doc_path.exists(), str(doc_path)):
            continue
        doc = LabelDocument.model_validate_json(doc_path.read_text())
        add(
            drug,
            "ids and tags",
            doc.doc_id == f"{SOURCE}:{pin.setid}"
            and doc.drugs == [drug]
            and doc.load_id.startswith(f"{SOURCE}-"),
            f"doc_id={doc.doc_id} drugs={doc.drugs}",
        )

        raw = Path(doc.raw_path)
        if not add(drug, "raw XML kept", raw.exists(), str(raw)):
            continue
        reparsed = parse_spl(raw.read_bytes())
        add(
            drug,
            "raw matches processed",
            reparsed["setid"] == pin.setid and reparsed["version"] == doc.source_version,
            "raw file is not the version described",
        )
        add(
            drug,
            "up to date with parser",
            reparsed["sections"] == doc.sections,
            "parser changed since this was loaded; rerun load",
        )

        add(drug, "has sections", len(doc.sections) >= 5, f"only {len(doc.sections)}")
        add(drug, "no empty sections", all(s.text.strip() for s in doc.sections), "empty text")
        add(
            drug,
            "boilerplate dropped",
            not any(s.section_code in SKIP_SECTION_CODES for s in doc.sections),
            "product-data or package-label section present",
        )
        if doc.doc_type_code == RX_DOCTYPE:
            add(
                drug,
                "Rx: interactions + contraindications",
                row["has_drug_interactions"] and row["has_contraindications"],
                f"interactions={row['has_drug_interactions']} "
                f"contraindications={row['has_contraindications']}",
            )
    return checks


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m drug_safety.ingest.dailymed")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("load", "suggest"):
        sp = sub.add_parser(name)
        sp.add_argument("--drug", action="append", help="limit to this drug (repeatable)")
    sub.add_parser("verify", help="offline acceptance checks on the loaded output")
    sub.add_parser("check-updates", help="network: is a newer version of any label out?")
    args = p.parse_args(argv)

    if args.cmd == "check-updates":
        rows = check_updates(DailyMedClient(), settings)
        stale = [r for r in rows if r["update_available"]]
        for r in rows:
            mark = "UPDATE" if r["update_available"] else "ok"
            print(
                f"{mark:7} {r['drug']:14} loaded v{r['loaded_version']:<4} newest "
                f"v{r['newest_version']:<4} ({r['newest_published']})"
            )
        print(f"\n{len(stale)} of {len(rows)} labels have a newer version.")
        if stale:
            print(
                "To take an update: `make ingest-dailymed`, then rerun the steps after it "
                "(chunking, and later embedding); each verify command shows what's stale."
            )
        print("RESULT:", "UPDATES AVAILABLE" if stale else "UP TO DATE")
        return 0

    if args.cmd == "verify":
        checks = verify(settings)
        failed = [c for c in checks if not c.ok]
        for c in failed:
            print(f"FAIL  {c.drug:14} {c.name}: {c.detail}")
        pins = load_pins()
        to_review = sorted(d for d, pin in pins.items() if pin.review == "check")
        missing = sorted(d for d, pin in pins.items() if pin.review == "missing")
        print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed.")
        print(f"Labels marked 'check' (human review, not a failure): {', '.join(to_review)}")
        print(f"Drugs with no US label: {', '.join(missing) or 'none'}")
        print("RESULT:", "PASS" if not failed else "FAIL")
        return 1 if failed else 0

    scope_drugs = load_scope().drugs
    drugs = args.drug or scope_drugs
    unknown = sorted(set(drugs) - set(scope_drugs))
    if unknown:
        p.error(f"not in scope/v1.yaml: {', '.join(unknown)}")

    client = DailyMedClient()
    if args.cmd == "suggest":
        pins = load_pins()
        for drug in drugs:
            query = (pins.get(drug) and pins[drug].query) or drug
            hit = suggest(client, query)
            print(
                f"{drug:14} "
                + (f"{hit[0]:8} {hit[1]['setid']}  {hit[1]['title']}" if hit else "NONE")
            )
        return 0

    manifest = run_load(drugs, client, settings)
    failed = [m["drug"] for m in manifest if m["status"] == "error"]
    print(json.dumps(manifest, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
