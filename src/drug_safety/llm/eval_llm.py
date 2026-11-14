"""LLM evaluation on the 10 synthetic patients (spec p22: faithfulness, fallback rate, latency).

    python -m drug_safety.llm.eval_llm build     # Mac, needs PostgreSQL: freeze the evidence
    python -m drug_safety.llm.eval_llm verify    # offline: cases, prompt sizes, fallback valid
    python -m drug_safety.llm.eval_llm run [--guided] [--label NAME]   # needs the model server
    python -m drug_safety.llm.eval_llm bundle    # zip for running `run` inside Colab

`build` runs the real retriever for each patient's flags (tests/gold/llm_cases.yaml), trims the
evidence to LLM_CONTEXT_TOKENS, and writes data/processed/llm/cases.json. `run` needs only that
file, the drug name list and the model, so it also runs in Colab, next to vLLM.

`run` writes data/outputs/<date>_llm_eval/<label>/:
    summary.json   first-try acceptance, final LLM share, fallback rate, latency, issues by rule
    <case>.json    the prompt, every attempt (answer, issues, seconds) and the final report
    review.md      each final report next to its evidence, for the manual faithfulness check
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import zipfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import yaml

from drug_safety.config import Settings, get_settings
from drug_safety.contracts import EvidenceChunk, Flag, Patient
from drug_safety.llm.drug_names import ALIASES_FILE, DrugNameFinder, names_path
from drug_safety.llm.explain import explain
from drug_safety.llm.fallback import fallback_report
from drug_safety.llm.prompts import PROMPT_VERSION, build_messages, patient_summary
from drug_safety.llm.validator import validate

CASES_FILE = Path("tests/gold/llm_cases.yaml")
FLAGS_FILE = Path("tests/gold/retrieval_flags.yaml")
QWEN_TOKENIZER = "Qwen/Qwen2.5-7B-Instruct"
# What the Colab notebook starts vLLM with (--max-model-len). 8192 was too small: the worst
# retry (p09: prompt + failed answer + errors + new answer) needs about 8,400 tokens.
MAX_MODEL_LEN = 16384


def frozen_path(settings: Settings) -> Path:
    return settings.data_dir / "processed" / "llm" / "cases.json"


def load_frozen(path: Path) -> list[dict]:
    rows = json.loads(path.read_text())["cases"]
    for r in rows:
        r["flags"] = [Flag.model_validate(f) for f in r["flags"]]
        r["evidence"] = {k: [EvidenceChunk.model_validate(c) for c in v]
                         for k, v in r["evidence"].items()}  # fmt: skip
    return rows


# ---------------------------------------------------------------- build (needs the database)


def build(settings: Settings) -> list[dict]:
    from drug_safety.db import connect
    from drug_safety.rag.chunking import bge_token_counter
    from drug_safety.rag.embeddings import Embedder
    from drug_safety.rag.retriever import PgRepository, build_context, retrieve

    gold = {r["case"]: r["flag"] for r in yaml.safe_load(FLAGS_FILE.read_text())["flags"]}
    embedder = Embedder(settings.embedding_model, settings.cache_dir)
    count = bge_token_counter(settings.embedding_model)
    out = []
    with connect() as conn:
        repo = PgRepository(conn)
        for p in yaml.safe_load(CASES_FILE.read_text())["patients"]:
            patient = Patient.model_validate(p["patient"])
            flags = [Flag.model_validate(gold[c]) for c in p["flags"]]
            found = {f.flag_id: retrieve(f, repo, embedder) for f in flags}
            kept = build_context(found, settings.llm_context_tokens, count)
            out.append({
                "case": p["id"],
                "summary": patient_summary(patient),
                "flags": [f.model_dump(mode="json", exclude_none=True) for f in flags],
                "evidence": {fid: [r.evidence.model_dump(mode="json") for r in rs]
                             for fid, rs in kept.items()},
                "retrieved": sum(len(v) for v in found.values()),
                "evidence_tokens": sum(count(r.evidence.text) for rs in kept.values() for r in rs),
            })  # fmt: skip
    path = frozen_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "prompt_version": PROMPT_VERSION,
        "context_tokens": settings.llm_context_tokens,
        "cases": out,
    }, indent=1))  # fmt: skip
    return out


# ---------------------------------------------------------------- verify (offline)


def qwen_counter():
    from tokenizers import Tokenizer

    tok = Tokenizer.from_pretrained(QWEN_TOKENIZER)
    return lambda text: len(tok.encode(text).ids)


def verify(settings: Settings) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    def add(name, ok, detail=""):
        checks.append((name, bool(ok), "" if ok else detail))

    path = frozen_path(settings)
    if not path.exists():
        add("frozen cases exist", False, "run `make llm-cases` (needs the database)")
        return checks
    try:
        finder = DrugNameFinder.load(settings)
    except FileNotFoundError as e:
        add("drug name list exists", False, str(e))
        return checks
    cases = load_frozen(path)
    want = yaml.safe_load(CASES_FILE.read_text())["patients"]
    add("frozen cases match tests/gold/llm_cases.yaml",
        [c["case"] for c in cases] == [p["id"] for p in want]
        and all([f.flag_id for f in c["flags"]] == [_fid(x) for x in p["flags"]]
                for c, p in zip(cases, want, strict=True)),
        "re-run `make llm-cases`")  # fmt: skip
    empty = [f"{c['case']}:{f.flag_id}" for c in cases for f in c["flags"]
             if not c["evidence"].get(f.flag_id)]  # fmt: skip
    add("every flag reaches the LLM with evidence", not empty, str(empty))
    over = [c["case"] for c in cases if c["evidence_tokens"] > settings.llm_context_tokens]
    add(f"evidence within {settings.llm_context_tokens} tokens per patient", not over, str(over))
    leaked = [c["case"] for c in cases if any(f"p{c['case'][1:3]}" == w for w in
              c["summary"].split())]  # fmt: skip
    add("patient summaries carry no patient_id", not leaked, str(leaked))

    count = qwen_counter()
    budget = MAX_MODEL_LEN - settings.model_max_tokens
    sizes = {}
    for c in cases:
        first = build_messages(c["summary"], c["flags"], c["evidence"])
        n = sum(count(m["content"]) for m in first) + 10 * len(first)
        # The retry adds the failed answer (at most model_max_tokens) and an error list.
        sizes[c["case"]] = (n, n + settings.model_max_tokens + 400)
    worst = max(r for _, r in sizes.values())
    add(f"prompt fits Qwen's {MAX_MODEL_LEN}-token window, retry included (worst {worst})",
        all(r + settings.model_max_tokens <= MAX_MODEL_LEN for _, r in sizes.values()),
        f"{sizes}")  # fmt: skip
    add(f"first prompt leaves {settings.model_max_tokens} tokens for the answer",
        all(n <= budget for n, _ in sizes.values()), str(sizes))  # fmt: skip

    bad = []
    for c in cases:
        fb = fallback_report(c["flags"], c["evidence"])
        _, issues = validate(fb.model_dump_json(), c["flags"], c["evidence"], c["summary"], finder)
        bad += [f"{c['case']}: {i.message}" for i in issues]
    add("template fallback passes the validator for every patient", not bad, str(bad[:5]))
    return checks


def _fid(case: str) -> str:
    gold = {
        r["case"]: r["flag"]["flag_id"] for r in yaml.safe_load(FLAGS_FILE.read_text())["flags"]
    }
    return gold[case]


# ---------------------------------------------------------------- run (needs the model)


def run(settings: Settings, label: str, limit: int | None, cases_path: Path | None) -> dict:
    from drug_safety.llm.client import chat_model, ping

    ok, detail = ping(settings)
    print(("OK   " if ok else "FAIL ") + detail)
    if not ok:
        raise SystemExit(1)
    cases = load_frozen(cases_path or frozen_path(settings))[:limit]
    finder = DrugNameFinder.load(settings)
    llm = chat_model(settings)
    out_dir = settings.outputs_dir / f"{date.today():%Y-%m-%d}_llm_eval" / label
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, review = [], [f"# LLM manual review: {label}\n",
                        "For each report: does every sentence follow from its flag and evidence? "
                        "Mark invented claims.\n"]  # fmt: skip
    for c in cases:
        r = explain(c["summary"], c["flags"], c["evidence"], llm, finder)
        rows.append(r)
        print(f"{c['case']:28} {r.report_source:8} attempts {len(r.attempts)}  "
              f"{sum(a.seconds for a in r.attempts):5.1f}s  "
              + "; ".join(i.message[:70] for a in r.attempts for i in a.issues)[:200])  # fmt: skip
        (out_dir / f"{c['case']}.json").write_text(json.dumps({
            "case": c["case"],
            # what was sent, so a later validator can re-judge this run exactly
            "summary": c["summary"],
            "flags": [f.model_dump(mode="json", exclude_none=True) for f in c["flags"]],
            "evidence": {k: [x.model_dump(mode="json") for x in v]
                         for k, v in c["evidence"].items()},
            "messages": build_messages(c["summary"], c["flags"], c["evidence"]),
            "result": r.model_dump(mode="json"),
        }, indent=1, ensure_ascii=False))  # fmt: skip
        review.append(_review_block(c, r))
    attempts = [a for r in rows for a in r.attempts]
    calls = [a.seconds for a in attempts if not a.error]
    first = Counter(i.rule for r in rows for i in r.attempts[0].issues)
    summary = {
        "label": label,
        "model": settings.model_name,
        "guided_json": settings.model_guided_json,
        "prompt_version": PROMPT_VERSION,
        "cases": len(rows),
        "accepted_first_try": sum(r.report_source == "llm" and len(r.attempts) == 1 for r in rows),
        "accepted_after_retry": sum(
            r.report_source == "llm" and len(r.attempts) == 2 for r in rows
        ),
        "fallback": sum(r.report_source == "template" for r in rows),
        "server_errors": sum(bool(a.error) for a in attempts),
        "first_try_issues_by_rule": dict(sorted(first.items())),
        "seconds_per_call": {"mean": round(statistics.mean(calls), 2) if calls else None,
                             "max": round(max(calls), 2) if calls else None},
        "seconds_per_patient_mean": round(
            statistics.mean(sum(a.seconds for a in r.attempts) for r in rows), 2),
    }  # fmt: skip
    n = len(rows)
    summary["faithfulness_first_try"] = f"{summary['accepted_first_try']}/{n}"
    summary["fallback_rate"] = f"{summary['fallback']}/{n}"
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "review.md").write_text("\n".join(review), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"\nResults in {out_dir}")
    return summary


def _review_block(c: dict, r) -> str:
    lines = [f"\n## {c['case']} ({r.report_source}, {len(r.attempts)} attempt(s))\n",
             "```\n" + c["summary"] + "\n```", f"**Summary:** {r.report.summary}\n"]  # fmt: skip
    by_flag = {e.flag_id: e for e in r.report.flags_explained}
    for f in c["flags"]:
        e = by_flag.get(f.flag_id)
        lines.append(f"### {f.flag_id} {f.drug} ({f.flag_type}, {f.severity}): {f.reason}")
        lines.append(f"**Explanation:** {e.plain_explanation if e else '-'}  ")
        lines.append(f"**Cites:** {', '.join(e.evidence_ids) if e else '-'}\n")
        for s in r.report.suggestions_for_clinician_review:
            if s.flag_id == f.flag_id:
                lines.append(f"- Suggestion: {s.text} ({', '.join(s.evidence_ids)})")
        for ch in c["evidence"].get(f.flag_id, []):
            lines.append(f"\n<details><summary>{ch.chunk_id}</summary>\n\n{ch.text}\n</details>")
        lines.append("\n- [ ] faithful   Invented claim(s): ...\n")
    if r.report.uncertainties:
        lines.append("**Uncertainties:** " + "; ".join(r.report.uncertainties))
    return "\n".join(lines)


# ---------------------------------------------------------------- bundle (for Colab)

BUNDLE_SRC = ["__init__.py", "config.py", "contracts", "llm"]


def bundle(settings: Settings) -> Path:
    root = Path("src/drug_safety")
    out = settings.outputs_dir / "llm_bundle.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in BUNDLE_SRC:
            p = root / item
            for f in [p] if p.is_file() else sorted(p.rglob("*")):
                if f.is_file() and "__pycache__" not in f.parts:
                    z.write(f, f.as_posix())
        z.write(ALIASES_FILE, ALIASES_FILE.as_posix())
        for f in (names_path(settings), frozen_path(settings)):
            z.write(f, f.relative_to(settings.data_dir.parent).as_posix()
                    if settings.data_dir.is_absolute() else f.as_posix())  # fmt: skip
    return out


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m drug_safety.llm.eval_llm")
    p.add_argument("cmd", choices=["build", "verify", "run", "bundle"])
    p.add_argument("--label", default=None, help="run: output folder name")
    p.add_argument("--guided", action="store_true", help="run: constrain output to the schema")
    p.add_argument("--limit", type=int, default=None, help="run: first N patients only")
    p.add_argument("--cases", type=Path, default=None, help="run: frozen cases file")
    args = p.parse_args(argv)
    settings = get_settings()
    if args.cmd == "build":
        for c in build(settings):
            print(f"{c['case']:28} {len(c['flags'])} flags, {c['retrieved']} chunks retrieved, "
                  f"{sum(len(v) for v in c['evidence'].values())} kept "
                  f"({c['evidence_tokens']} tokens)")  # fmt: skip
        print(f"written {frozen_path(settings)}")
        return 0
    if args.cmd == "verify":
        checks = verify(settings)
        for name, ok, detail in checks:
            print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else ""))
        failed = sum(not ok for _, ok, _ in checks)
        print(f"\n{len(checks) - failed}/{len(checks)} checks passed.")
        print("RESULT:", "PASS" if not failed else "FAIL")
        return 1 if failed else 0
    if args.cmd == "bundle":
        print(f"written {bundle(settings)}")
        return 0
    if args.guided:
        settings = settings.model_copy(update={"model_guided_json": True})
    label = args.label or ("guided" if settings.model_guided_json else "plain")
    run(settings, label, args.limit, args.cases)
    return 0


if __name__ == "__main__":
    sys.exit(main())
