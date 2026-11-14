"""Checks, review sheet and export for curated rules (lab_drug_rules, lifestyle_drug).

Usage:
    python -m drug_safety.curation.rules verify   # every check; ends RESULT: PASS or FAIL
    python -m drug_safety.curation.rules review   # writes docs/curation_review.md for a reviewer
    python -m drug_safety.curation.rules export   # APPROVED rows only -> CSV seeds for Person A

A row only counts as evidence-backed if its quote is really in its chunk, from its own drug's
label, its numbers are in the quote, and its severity follows the phrase table.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

import yaml
from pydantic import BaseModel

from drug_safety.config import Settings, get_settings
from drug_safety.contracts import Lifestyle, Severity

CURATION = Path("curation")
FILES = {"lab": CURATION / "lab_drug_rules.yaml", "lifestyle": CURATION / "lifestyle_drug.yaml"}
STATUSES = {"draft", "approved", "rejected"}
_LESS = r"(below|less than|<|under|falls below|between \d+ to|between \d+ and|\d+\s*[–-]\s*)"


def _norm(s: str) -> str:
    return " ".join(s.split())


def _yaml(path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


class Check(BaseModel):
    rule_id: str
    name: str
    ok: bool
    detail: str = ""


def load_context(settings: Settings) -> dict:
    chunks = {}
    with open(settings.data_dir / "processed" / "chunks" / "dailymed.jsonl", encoding="utf-8") as f:
        for line in f:
            c = json.loads(line)
            chunks[c["chunk_id"]] = c
    manifest = json.loads(
        (settings.data_dir / "processed" / "chunks" / "dailymed_manifest.json").read_text()
    )
    return {
        "chunks": chunks,
        "label_of": {r["doc_id"]: r["drug"] for r in manifest},
        "rxcui": _yaml("scope/rxcui.yaml")["rxcui"],
        "labs": _yaml("scope/lab_codes.yaml")["measures"],
        "severity": _yaml(CURATION / "severity_rules.yaml")["phrases"],
        "units": _yaml(CURATION / "units.yaml")["conversions"],
        "diet_tags": set(_yaml(CURATION / "diet_tags.yaml")["tags"]),
    }


def _common(row: dict, ctx: dict, add) -> None:
    drug = row.get("drug")
    add(
        "drug is a v1 drug with the verified RxCUI",
        ctx["rxcui"].get(drug) == row.get("rxcui"),
        f"{drug}: {row.get('rxcui')} vs {ctx['rxcui'].get(drug)}",
    )
    add("status is draft, approved or rejected", row.get("status") in STATUSES, row.get("status"))
    if row.get("status") == "approved":
        add(
            "approved rows name a reviewer and a date",
            bool(row.get("reviewed_by")) and bool(row.get("reviewed_on")),
            "missing",
        )
    evidence = row.get("evidence") or []
    add("has at least one quote", bool(evidence), "no evidence")
    for i, e in enumerate(evidence, 1):
        c = ctx["chunks"].get(e.get("chunk_id"))
        if not c:
            add(f"quote {i}: chunk exists", False, f"{e.get('chunk_id')} not in the chunk file")
            continue
        add(
            f"quote {i}: text is in its chunk",
            _norm(e["quote"]) in _norm(c["text"]),
            f"not found in {e['chunk_id']}",
        )
        add(
            f"quote {i}: chunk is from {drug}'s own label",
            ctx["label_of"][c["doc_id"]] == drug,
            f"chunk is from {ctx['label_of'][c['doc_id']]}'s label",
        )
    quotes = " ".join(_norm(e["quote"]) for e in evidence).lower()
    basis = (row.get("severity_basis") or "").lower()
    add(
        "severity is on the 4-level scale",
        row.get("severity") in Severity.__members__,
        row.get("severity"),
    )
    add("severity basis is a listed phrase", basis in ctx["severity"], f"'{basis}' not listed")
    if basis in ctx["severity"]:
        if basis == "boxed warning":
            in_box = any(
                ctx["chunks"].get(e["chunk_id"], {}).get("section") == "boxed_warning"
                for e in evidence
            )
            add(
                "severity basis 'boxed warning': a quote is from a boxed warning",
                in_box,
                "no quote from a boxed_warning section",
            )
        else:
            add(
                "severity basis phrase appears in a quote (spacing ignored)",
                basis.replace(" ", "") in quotes.replace(" ", ""),
                f"'{basis}' not in the quotes",
            )
        expected = ctx["severity"][basis]
        add(
            "severity matches the phrase table",
            row.get("severity") == expected or bool(row.get("severity_override_reason")),
            f"'{basis}' means {expected}, row says {row.get('severity')}",
        )


def check_lab(row: dict, ctx: dict, add) -> None:
    _common(row, ctx, add)
    m = ctx["labs"].get(row.get("measure"))
    add("measure is a known v1 lab measure", m is not None, row.get("measure"))
    add("operator is < <= > >=", row.get("operator") in ("<", "<=", ">", ">="), row.get("operator"))
    quotes = " ".join(_norm(e["quote"]) for e in row.get("evidence", []))
    n = str(row.get("label_threshold"))
    add(
        "label number appears in a quote",
        re.search(rf"(?<![\d.]){re.escape(n)}(?![\d.])", quotes) is not None,
        f"'{n}' not in the quotes",
    )
    add(
        "label unit appears in a quote",
        _norm(row.get("label_unit", "")) in quotes,
        f"'{row.get('label_unit')}' not in the quotes",
    )
    if m:
        conv = [
            c
            for c in ctx["units"]
            if c["label_unit"] == row.get("label_unit") and c["unit"] == m["unit"]
        ]
        if not add(
            "label unit converts to the canonical unit",
            bool(conv),
            f"{row.get('label_unit')} -> {m['unit']}",
        ):
            return
        c = conv[0]
        want = round(float(n) * c["factor"], 6)
        upper = row["operator"] in ("<", "<=")
        # A label range's upper bound (e.g. 15-29, 500-999) is written as < the next whole number.
        ok = abs(row["threshold"] - want) < 1e-9 or (
            upper and abs(row["threshold"] - round((float(n) + 1) * c["factor"], 6)) < 1e-9
        )
        add(
            "threshold equals the label number in the canonical unit",
            ok,
            f"label {n} {row.get('label_unit')} -> {want} {m['unit']}, row says {row['threshold']}",
        )
        # A proxy depends on what the label MEASURES (CLcr), not on its unit: allopurinol's
        # table is eGFR in mL/minute.
        if row.get("label_measure") == "CLcr" and row.get("measure") == "egfr":
            add(
                "proxy measure is explained (proxy_note)",
                bool(row.get("proxy_note")),
                "label states CLcr; say why eGFR is used",
            )
        add(
            "no proxy_note unless the label measure differs from the rule's",
            row.get("label_measure") == "CLcr" or not row.get("proxy_note"),
            "proxy_note on a row whose label already uses the rule's measure",
        )
    # The words next to the label number must agree with the rule's direction.
    below = (
        re.search(rf"{_LESS}[^.;|]{{0,25}}{re.escape(n)}", quotes, re.I)
        or re.search(rf"{re.escape(n)}\s*(mL|/μL)?[^.;|]{{0,15}}(or less|or below)", quotes, re.I)
        or re.search(rf"to\s*{re.escape(n)}", quotes, re.I)  # "> 30 to 60" (upper bound)
    )
    above = re.search(
        rf"(above|greater than|more than|exceeds?|>|≥)\s*[^.;|]{{0,10}}{re.escape(n)}(?!\s*to)",
        quotes,
        re.I,
    )
    if row.get("operator") in ("<", "<="):
        add(
            "quote wording agrees with the operator (below / less than / up to)",
            below is not None,
            f"no 'below/less than/up to' next to {n}",
        )
    elif row.get("operator") in (">", ">="):
        add(
            "quote wording agrees with the operator (above / greater than)",
            above is not None and below is None,
            f"the quote doesn't say 'above/greater than' {n}",
        )
    opn = {"<": "lt", "<=": "le", ">": "gt", ">=": "ge"}.get(row.get("operator"), "?")
    t = f"{row.get('threshold', 0):g}".replace(".", "_")
    add(
        "rule_id matches the row",
        row.get("rule_id") == f"lab_drug:{row.get('measure')}:{opn}{t}:{row.get('drug')}",
        row.get("rule_id"),
    )


def check_lifestyle(row: dict, ctx: dict, add) -> None:
    _common(row, ctx, add)
    ft, vals = row.get("factor_type"), row.get("factor_values") or []
    allowed = {
        "smoking": set(Lifestyle.model_fields["smoking"].annotation.__args__[0].__args__),
        "alcohol": set(Lifestyle.model_fields["alcohol"].annotation.__args__[0].__args__),
        "diet_tag": ctx["diet_tags"],
    }
    add("factor_type is smoking, alcohol or diet_tag", ft in allowed, ft)
    if ft in allowed:
        add(
            "factor values are allowed by the Patient contract / diet tag list",
            bool(vals) and set(vals) <= allowed[ft],
            f"{vals} vs {sorted(allowed[ft])}",
        )
        add(
            "no rule fires for a non-user ('never' smoker, 'none' alcohol)",
            not set(vals) & {"never", "none"},
            str(vals),
        )
    key = "-".join(vals)  # e.g. "regular-heavy": the ID says exactly who the rule covers
    add(
        "rule_id matches the row",
        row.get("rule_id") == f"lifestyle_drug:{ft}:{key}:{row.get('drug')}",
        row.get("rule_id"),
    )
    add(
        "effect and mechanism filled",
        bool(row.get("effect")) and bool(row.get("mechanism")),
        "missing",
    )


# Rules the spec's examples rely on; if one disappears, verify fails.
REQUIRED = {
    "lab_drug:egfr:lt30:metformin": "spec worked example (eGFR about 28)",
    "lifestyle_drug:smoking:current:clozapine": "spec rule-engine example (smoker on clozapine)",
    "lifestyle_drug:diet_tag:high_vitamin_k:warfarin": "spec diet-tag example (high_vitamin_k)",
    "lifestyle_drug:diet_tag:grapefruit:simvastatin": "spec diet-tag example (grapefruit)",
}


def verify(settings: Settings) -> list[Check]:
    ctx = load_context(settings)
    checks: list[Check] = []
    ids: list[str] = []
    for kind, path in FILES.items():
        for row in _yaml(path)["rules"]:
            rid = row.get("rule_id", "?")
            ids.append(rid)

            def add(name, ok, detail="", rid=rid):
                checks.append(
                    Check(rule_id=rid, name=name, ok=bool(ok), detail="" if ok else str(detail))
                )
                return ok

            (check_lab if kind == "lab" else check_lifestyle)(row, ctx, add)
            flags = row.get("review_flags", [])
            add(
                "review_flags is a list of questions",
                isinstance(flags, list) and all(isinstance(f, str) and f for f in flags),
                flags,
            )
            if row.get("status") == "approved" and flags:
                add(
                    "approved rows have their review flags answered (review_answer)",
                    bool(row.get("review_answer")),
                    "set review_answer when approving",
                )
    # Coverage (spec: empty cells are reviewed, not ignored).
    notes = _yaml(CURATION / "coverage_notes.yaml")
    for kind, table in (("lab", "lab"), ("lifestyle", "lifestyle")):
        with_rows = {r["drug"] for r in _yaml(FILES[kind])["rules"] if r["status"] != "rejected"}
        noted = set(notes.get(table) or {})
        for d in ctx["rxcui"]:
            ok = (d in with_rows) != (d in noted)
            checks.append(
                Check(
                    rule_id=f"coverage:{table}:{d}",
                    name=f"{table}: {d} has rows or a coverage note",
                    ok=ok,
                    detail=""
                    if ok
                    else ("rows AND a note" if d in noted else "no rows and no note"),
                )
            )
    dup = sorted({i for i in ids if ids.count(i) > 1})
    checks.append(Check(rule_id="*", name="rule_ids unique", ok=not dup, detail=str(dup)))
    for rid, why in REQUIRED.items():
        checks.append(
            Check(
                rule_id=rid, name=f"required rule present ({why})", ok=rid in ids, detail="missing"
            )
        )
    return checks


# ---------------------------------------------------------------- review sheet and export


def review_sheet(settings: Settings) -> str:
    ctx = load_context(settings)
    out = [
        "# Curated rules: review sheet",
        "",
        "Generated by `make curation-review` from `curation/*.yaml`. For each row, check the "
        "quote supports the rule, the threshold and the severity. To approve, edit the YAML: "
        "`status: approved`, `reviewed_by: <name>`, `reviewed_on: <YYYY-MM-DD>`.",
        "",
    ]
    for kind, path in FILES.items():
        rows = _yaml(path)["rules"]
        out += [
            f"## {'Lab-drug rules' if kind == 'lab' else 'Lifestyle-drug rules'} ({len(rows)})",
            "",
        ]
        for r in rows:
            if kind == "lab":
                what = (
                    f"**{r['drug']}**: {r['measure']} {r['operator']} {r['threshold']} "
                    f"{ctx['labs'][r['measure']]['unit']} → {r['action']}"
                )
            else:
                what = (
                    f"**{r['drug']}**: {r['factor_type']} = {', '.join(r['factor_values'])} "
                    f"→ {r['effect']}"
                )
            out += [f"### `{r['rule_id']}` · {r['severity']} · {r['status']}", "", what, ""]
            for f in r.get("review_flags", []):
                out += [f"**⚠ For the reviewer:** {f}", ""]
            for e in r["evidence"]:
                c = ctx["chunks"][e["chunk_id"]]
                head = " > ".join(c["headings"][0])
                out.append(f"> {e['quote']}  \n> — {head} (`{e['chunk_id']}`)")
                out.append("")
            for k in ("proxy_note", "notes"):
                if r.get(k):
                    out += [f"*{k.replace('_', ' ')}:* {r[k]}", ""]
    notes = _yaml(CURATION / "coverage_notes.yaml")
    out += [
        "## Coverage: drugs with no rule in a table, and why",
        "",
        "| Drug | Lab-drug | Lifestyle-drug |",
        "|---|---|---|",
    ]
    rows_by = {k: {r["drug"] for r in _yaml(FILES[k])["rules"]} for k in FILES}
    for d in ctx["rxcui"]:
        cells = []
        for k in ("lab", "lifestyle"):
            n = sum(r["drug"] == d for r in _yaml(FILES[k])["rules"])
            cells.append(f"{n} rule(s)" if d in rows_by[k] else f"none: {notes[k][d]}")
        out.append(f"| {d} | {cells[0]} | {cells[1]} |")
    return "\n".join(out) + "\n"


def export(settings: Settings) -> dict[str, int]:
    """Approved rows only, flattened to the spec's columns, as CSV seeds for Person A."""
    ctx = load_context(settings)
    out_dir = settings.data_dir / "processed" / "curation"
    out_dir.mkdir(parents=True, exist_ok=True)
    counts = {}
    for kind, path in FILES.items():
        rows = [r for r in _yaml(path)["rules"] if r["status"] == "approved"]
        flat = []
        for r in rows:
            c = ctx["chunks"][r["evidence"][0]["chunk_id"]]
            base = {
                "rule_id": r["rule_id"],
                "rxcui_ingredient": r["rxcui"],
                "drug": r["drug"],
                "severity": r["severity"],
                "source": "label (DailyMed)",
                "source_version": c["source_version"],
                "license": c["license"],
                "evidence_chunk_id": r["evidence"][0]["chunk_id"],
                "reviewed_by": r.get("reviewed_by"),
                "reviewed_on": r.get("reviewed_on"),
            }
            if kind == "lab":
                m = ctx["labs"][r["measure"]]
                flat.append(
                    base
                    | {
                        "loinc": "" if m.get("derived") else m["loinc"],
                        "derived_measure": r["measure"] if m.get("derived") else "",
                        "operator": r["operator"],
                        "threshold": r["threshold"],
                        "unit": m["unit"],
                        "action": r["action"],
                    }
                )
            else:
                for v in r["factor_values"]:
                    flat.append(
                        base
                        | {
                            "factor_type": r["factor_type"],
                            "factor_value": v,
                            "effect": r["effect"],
                            "mechanism": r["mechanism"],
                        }
                    )
        name = "lab_drug_rules" if kind == "lab" else "lifestyle_drug"
        with open(out_dir / f"{name}.csv", "w", newline="", encoding="utf-8") as f:
            if flat:
                w = csv.DictWriter(f, fieldnames=list(flat[0]))
                w.writeheader()
                w.writerows(flat)
        counts[name] = len(flat)
    return counts


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    cmd = (argv or sys.argv[1:] or ["verify"])[0]
    if cmd == "review":
        Path("docs/curation_review.md").write_text(review_sheet(settings), encoding="utf-8")
        print("wrote docs/curation_review.md")
        return 0
    if cmd == "export":
        print("exported APPROVED rows only:", export(settings))
        return 0
    checks = verify(settings)
    failed = [c for c in checks if not c.ok]
    for c in failed:
        print(f"FAIL  {c.rule_id}  {c.name}: {c.detail}")
    status = {}
    for path in FILES.values():
        for r in _yaml(path)["rules"]:
            status[r["status"]] = status.get(r["status"], 0) + 1
    print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed. Rows by status: {status}")
    print("RESULT:", "PASS" if not failed else "FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
