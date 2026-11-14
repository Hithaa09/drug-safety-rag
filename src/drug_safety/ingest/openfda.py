"""openFDA loader: the extra, harmonised fields for each pinned label (NOT the label text,
which is identical to DailyMed's).

Usage:
    python -m drug_safety.ingest.openfda fetch    # network: fields, labeler check, proposals
    python -m drug_safety.ingest.openfda verify   # offline acceptance checks

Drug classes are NOT taken from openFDA: its class fields were wrong for several v1 drugs
(metformin listed as a sulfonylurea, combination-product classes on amlodipine and paracetamol)
and empty for others. Classes come from NLM RxClass instead (ingest/rxclass.py).

For each pinned label it records:
  - the application number, its holder, and who made this label: the manufacturer or holder,
    a repackager, or unclear;
  - IDs (RxCUI, NDC, UNII, brand) as a cross-check for Person A.
If a pin is a repackager's copy (or unclear) and the holder has its own label, it PROPOSES that
label. Pins are never changed automatically.

Writes:
    data/raw/openfda/labels/<setid>.json            untouched openFDA label response
    data/raw/openfda/applications/<number>.json     untouched drugs@FDA response
    data/processed/openfda/enrichment.json          one row per drug
    data/processed/openfda/ids_for_person_a.csv     RxCUI / NDC / UNII / brand cross-check
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import logging
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from lxml import etree
from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.ingest.dailymed import NS, USER_AGENT, load_pins
from drug_safety.scope import load_scope

log = logging.getLogger(__name__)

BASE_URL = "https://api.fda.gov"
SOURCE = "openfda"
LICENSE = "openFDA, CC0 1.0 (https://open.fda.gov/license/)"

# Words that don't identify a company ("Pharma", "Inc", "USA" ...).
_COMPANY_NOISE = {
    "PHARMA",
    "PHARMS",
    "PHARMACEUTICAL",
    "PHARMACEUTICALS",
    "LABS",
    "LABORATORIES",
    "INC",
    "LLC",
    "LTD",
    "LIMITED",
    "CORP",
    "CORPORATION",
    "CO",
    "COMPANY",
    "USA",
    "US",
    "U",
    "S",
    "HOLDINGS",
    "GROUP",
    "THE",
    "OF",
    "AND",
    "INDUSTRIES",
    "PLC",
    "AG",
    "GMBH",
    "SA",
    "NV",
}
REPACK_OPS = {"REPACK", "RELABEL"}
MAKE_OPS = {"MANUFACTURE", "API MANUFACTURE"}


class Alternative(BaseModel):
    setid: str
    manufacturer: str
    effective_time: str
    version: str


class Enrichment(BaseModel):
    drug: str
    setid: str
    in_openfda: bool
    manufacturer: str | None = None
    application_number: str | None = None
    application_holder: str | None = None
    labeler_operations: list[str] = []  # from the label's own XML (DailyMed raw file)
    labeler_role: str = "unclear"  # holder_or_manufacturer | repackager | unclear
    proposed_label: Alternative | None = None
    brand_name: list[str] = []
    generic_name: list[str] = []
    rxcui: list[str] = []
    product_ndc: list[str] = []
    unii: list[str] = []
    route: list[str] = []
    product_type: list[str] = []
    openfda_version: str | None = None
    note: str | None = None
    retrieved_at: str
    license: str = LICENSE


# ---------------------------------------------------------------- who made the label


def company_key(name: str | None) -> set[str]:
    """Identifying words of a company name: 'GLENMARK PHARMS LTD' -> {'GLENMARK'}."""
    words = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper()).split()
    return {w for w in words if w not in _COMPANY_NOISE and len(w) > 1}


def same_company(a: str | None, b: str | None) -> bool:
    ka, kb = company_key(a), company_key(b)
    return bool(ka and kb and (ka & kb))


def labeler_operations(xml: bytes) -> list[str]:
    """Business operations the label's author lists for itself (e.g. MANUFACTURE, REPACK)."""
    root = etree.fromstring(xml)
    ops = {
        (c.get("displayName") or "").upper()
        for c in root.iterfind(".//v:author//v:performance/v:actDefinition/v:code", NS)
    }
    return sorted(o for o in ops if o)


def labeler_role(manufacturer: str | None, holder: str | None, ops: list[str]) -> str:
    ops_set = set(ops)
    if ops_set & MAKE_OPS or same_company(manufacturer, holder):
        return "holder_or_manufacturer"
    if ops_set & REPACK_OPS or re.search(r"REPACK|RELABEL", (manufacturer or "").upper()):
        return "repackager"
    return "unclear"


# ---------------------------------------------------------------- HTTP


class OpenFDAClient:
    """openFDA without a key allows 240 requests/minute and 1,000/day; we pace at ~3/s."""

    def __init__(self, api_key: str | None = None, delay: float = 0.3, timeout: float = 30):
        self.api_key = api_key
        self.delay = delay
        self.http = httpx.Client(timeout=timeout, headers={"User-Agent": USER_AGENT})

    def get(self, path: str, search: str, limit: int = 1) -> dict | None:
        params = {"search": search, "limit": limit}
        if self.api_key:
            params["api_key"] = self.api_key
        for attempt in range(4):
            time.sleep(self.delay)
            try:
                r = self.http.get(f"{BASE_URL}{path}", params=params)
            except httpx.TransportError as e:
                log.warning("network error %s (attempt %d)", e, attempt + 1)
            else:
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 404:  # openFDA's answer for "no matches"
                    return None
                if r.status_code not in (429, 500, 502, 503, 504):
                    r.raise_for_status()
                log.warning("HTTP %d for %s (attempt %d)", r.status_code, path, attempt + 1)
            time.sleep(2**attempt)
        raise RuntimeError(f"openFDA request failed after retries: {path} {search}")

    def label(self, setid: str) -> dict | None:
        return self.get("/drug/label.json", f'set_id:"{setid}"')

    def application(self, number: str) -> dict | None:
        return self.get("/drug/drugsfda.json", f'application_number:"{number}"')

    def labels_for_application(self, number: str) -> list[dict]:
        body = self.get("/drug/label.json", f'openfda.application_number:"{number}"', limit=100)
        return body["results"] if body else []


# ---------------------------------------------------------------- enrichment


def _save(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=1), encoding="utf-8")


def _dailymed_raw(settings: Settings, setid: str) -> bytes | None:
    files = sorted(glob.glob(str(settings.raw_dir / "dailymed" / setid / "v*.xml")))
    return Path(files[-1]).read_bytes() if files else None


def enrich_one(drug: str, setid: str, client: OpenFDAClient, settings: Settings) -> Enrichment:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    raw_dir = settings.raw_dir / SOURCE
    row = Enrichment(drug=drug, setid=setid, in_openfda=False, retrieved_at=now)

    xml = _dailymed_raw(settings, setid)
    row.labeler_operations = labeler_operations(xml) if xml else []

    body = client.label(setid)
    if body is None:
        row.note = "label not in openFDA"
        return row
    _save(raw_dir / "labels" / f"{setid}.json", body)
    res = body["results"][0]
    of = res.get("openfda", {})
    row.in_openfda = True
    row.openfda_version = str(res.get("version"))
    row.manufacturer = (of.get("manufacturer_name") or [None])[0]
    row.application_number = (of.get("application_number") or [None])[0]
    for k in (
        "brand_name",
        "generic_name",
        "rxcui",
        "product_ndc",
        "unii",
        "route",
        "product_type",
    ):
        setattr(row, k, of.get(k, []))

    if row.application_number:
        app = client.application(row.application_number)
        if app:
            _save(raw_dir / "applications" / f"{row.application_number}.json", app)
            row.application_holder = app["results"][0].get("sponsor_name")
    row.labeler_role = labeler_role(
        row.manufacturer, row.application_holder, row.labeler_operations
    )
    if not row.application_number:
        row.note = "no application number (OTC monograph product)"
        return row
    if row.labeler_role == "holder_or_manufacturer" or not row.application_holder:
        return row

    # A copy (or unclear): look for the holder's own oral label under the same application.
    own = [
        x
        for x in client.labels_for_application(row.application_number)
        if same_company(
            (x.get("openfda", {}).get("manufacturer_name") or [""])[0], row.application_holder
        )
        and "ORAL" in x.get("openfda", {}).get("route", [])
    ]
    if own:
        best = max(own, key=lambda x: x.get("effective_time", ""))
        row.proposed_label = Alternative(
            setid=best["set_id"],
            manufacturer=best["openfda"]["manufacturer_name"][0],
            effective_time=best.get("effective_time", ""),
            version=str(best.get("version")),
        )
    elif row.labeler_role == "repackager":
        row.note = "repackager copy, but the holder has no label of its own in openFDA"
    else:
        row.note = (
            f"label by {row.manufacturer}, application held by {row.application_holder}; "
            "likely the same company under another name; check by eye"
        )
    return row


def _out_dir(settings: Settings) -> Path:
    return settings.data_dir / "processed" / SOURCE


def run_fetch(client: OpenFDAClient, settings: Settings) -> list[Enrichment]:
    pins = load_pins()
    rows = []
    for drug in load_scope().drugs:
        pin = pins.get(drug)
        if not pin or not pin.setid:
            continue
        try:
            rows.append(enrich_one(drug, pin.setid, client, settings))
        except Exception as e:
            log.exception("failed: %s", drug)
            rows.append(
                Enrichment(
                    drug=drug,
                    setid=pin.setid,
                    in_openfda=False,
                    retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"),
                    note=f"error: {e}",
                )
            )
        log.info("%-14s %s", drug, rows[-1].labeler_role)

    out = _out_dir(settings)
    out.mkdir(parents=True, exist_ok=True)
    (out / "enrichment.json").write_text(
        json.dumps([r.model_dump() for r in rows], indent=2), encoding="utf-8"
    )
    with open(out / "ids_for_person_a.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["drug", "setid", "rxcui", "product_ndc", "unii", "brand_name", "source"])
        for r in rows:
            if r.in_openfda:
                w.writerow(
                    [
                        r.drug,
                        r.setid,
                        ";".join(r.rxcui),
                        ";".join(r.product_ndc),
                        ";".join(r.unii),
                        ";".join(r.brand_name),
                        "openFDA (CC0)",
                    ]
                )
    return rows


def read_enrichment(settings: Settings) -> list[Enrichment]:
    path = _out_dir(settings) / "enrichment.json"
    return [Enrichment.model_validate(r) for r in json.loads(path.read_text())]


# ---------------------------------------------------------------- verify


class Check(BaseModel):
    name: str
    ok: bool
    detail: str = ""


# Expected gaps, with the reason. verify fails on any gap not listed here.
EXPECTED_NOT_IN_OPENFDA = {"paracetamol": "this OTC label isn't in openFDA's label set"}
EXPECTED_NO_RXCUI = {"aspirin", "amitriptyline"}  # openFDA leaves rxcui empty on these labels


def verify(settings: Settings) -> list[Check]:
    checks: list[Check] = []

    def add(name, ok, detail=""):
        checks.append(Check(name=name, ok=ok, detail="" if ok else detail))

    path = _out_dir(settings) / "enrichment.json"
    if not path.exists():
        add("enrichment file exists", False, "run `make fetch-openfda`")
        return checks
    rows = {r.drug: r for r in read_enrichment(settings)}
    pins = load_pins()
    pinned = {d for d, p in pins.items() if p.setid}

    add("one row per pinned label", set(rows) == pinned, f"differs: {sorted(set(rows) ^ pinned)}")
    add(
        "rows match current pins",
        all(rows[d].setid == pins[d].setid for d in rows if d in pins),
        "a pin changed since fetch; run `make fetch-openfda`",
    )
    errors = [d for d, r in rows.items() if r.note and r.note.startswith("error")]
    add("no fetch errors", not errors, str(errors))
    absent = {d for d, r in rows.items() if not r.in_openfda}
    add(
        "only expected labels missing from openFDA",
        absent <= set(EXPECTED_NOT_IN_OPENFDA),
        f"unexpected: {sorted(absent - set(EXPECTED_NOT_IN_OPENFDA))}",
    )
    manifest = _dailymed_manifest(settings)
    for d, r in rows.items():
        if not r.in_openfda:
            continue
        raw = settings.raw_dir / SOURCE / "labels" / f"{r.setid}.json"
        add(f"{d}: raw response kept", raw.exists(), str(raw))
        add(
            f"{d}: same label version as DailyMed",
            r.openfda_version == manifest.get(d, {}).get("version"),
            f"openFDA v{r.openfda_version} vs DailyMed v{manifest.get(d, {}).get('version')}",
        )
        add(f"{d}: has RxCUI", bool(r.rxcui) or d in EXPECTED_NO_RXCUI, "no RxCUI")
        add(
            f"{d}: role matches the evidence",
            r.labeler_role
            == labeler_role(r.manufacturer, r.application_holder, r.labeler_operations),
            r.labeler_role,
        )
        if r.proposed_label:
            add(
                f"{d}: proposal is the holder's own label",
                same_company(r.proposed_label.manufacturer, r.application_holder),
                f"{r.proposed_label.manufacturer} vs {r.application_holder}",
            )
    return checks


def _dailymed_manifest(settings: Settings) -> dict:
    path = settings.data_dir / "processed" / "dailymed" / "_manifest.json"
    return {r["drug"]: r for r in json.loads(path.read_text())} if path.exists() else {}


# ---------------------------------------------------------------- CLI


def report(rows: list[Enrichment]) -> None:
    print(f"{'drug':14} {'labeler':22} proposal / note")
    for r in rows:
        prop = (
            f"USE {r.proposed_label.setid} ({r.proposed_label.manufacturer})"
            if r.proposed_label
            else (r.note or "")
        )
        print(f"{r.drug:14} {r.labeler_role:22} {prop}")


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="python -m drug_safety.ingest.openfda")
    p.add_argument("cmd", choices=["fetch", "verify", "report"])
    args = p.parse_args(argv)
    if args.cmd == "fetch":
        rows = run_fetch(OpenFDAClient(api_key=settings.openfda_api_key), settings)
        report(rows)
        n = sum(1 for r in rows if r.proposed_label)
        print(
            f"\n{n} label swap(s) proposed. Nothing was changed: to accept one, edit "
            "scope/dailymed_labels.yaml (see the ingest README)."
        )
        return 0
    if args.cmd == "report":
        report(read_enrichment(settings))
        return 0
    checks = verify(settings)
    failed = [c for c in checks if not c.ok]
    for c in failed:
        print(f"FAIL  {c.name}: {c.detail}")
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed.")
    print("RESULT:", "PASS" if not failed else "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
