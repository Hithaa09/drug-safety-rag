"""Person B's evaluation report (spec "Evaluation of the full pipeline" and "Definition of done").

    python -m drug_safety.evaluation.report     # needs PostgreSQL; never calls the model

Writes data/outputs/<date>_evaluation/report.md and metrics.json:
  1. Retrieval hit rate: gold flags whose expected guideline or label chunk is in the top 3.
  2. LLM faithfulness (share accepted on the first try) and fallback rate, from every Colab run in
     data/outputs/*_llm_eval/, each also re-judged by the current validator. Runs on an older
     prompt are marked as such.
  3. Latency per patient: measured here without the LLM (retrieval + context); with the LLM =
     that plus the model time measured in the latest run.
  4. Definition of done, Person B's items: PASS, FAIL (with the reason) or PENDING (what it waits
     for).
Flag recall and precision are Person A's (rule engine) and are not computed here.
"""

from __future__ import annotations

import glob
import json
import statistics
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from drug_safety.config import Settings, get_settings
from drug_safety.contracts import EvidenceChunk, Flag

FIXTURES = Path("tests/llm/fixtures")
MANUAL_REVIEW = Path("docs/llm_manual_review.md")
MODULE_TESTS = ["contracts", "ingest", "rag", "llm", "ui", "evaluation"]
PAPERS_REQUIRED = 500  # spec definition of done, item 2


# ---------------------------------------------------------------- 1. retrieval


def retrieval(s: Settings, embedder) -> dict:
    from drug_safety.rag.eval_flags import evaluate

    rows, failures, _ = evaluate(s, embedder)
    gene = [r for r in rows if r["flag_type"] == "gene_drug"]
    by_type = {}
    for r in rows:
        t = by_type.setdefault(r["flag_type"], [0, 0])
        t[0] += r["hit"]
        t[1] += 1
    return {
        "hit": sum(r["hit"] for r in rows),
        "cases": len(rows),
        "label_hit": sum(r["label"] for r in rows),
        "cpic_hit": sum(r["cpic"] for r in gene),
        "gene_flags": len(gene),
        "by_type": by_type,
        "with_paper": sum(r["paper"] is not None for r in rows),
        "failures": failures,
        "misses": [r["case"] for r in rows if not r["hit"]],
    }


# ---------------------------------------------------------------- 2. LLM runs


def _rejudge_inputs(case_file: dict) -> tuple | None:
    """(summary, flags, evidence) the run used: saved in the case file since step 10, or found
    in a test fixture holding exactly the same answers (run 1)."""
    if "evidence" in case_file:
        return (case_file["summary"], case_file["flags"], case_file["evidence"])
    answers = [a["answer"] for a in case_file["result"]["attempts"]]
    for p in sorted(FIXTURES.glob("*.json")):
        for c in json.loads(p.read_text())["cases"]:
            if c["case"] == case_file["case"] and c["answers"] == answers:
                return (c["summary"], c["flags"], c["evidence"])
    return None


def rejudge(case_file: dict, finder) -> dict | None:
    """The current validator's verdict on each attempt of one case."""
    from drug_safety.llm.validator import validate

    inputs = _rejudge_inputs(case_file)
    if inputs is None:
        return None
    summary, flags, evidence = inputs
    flags = [Flag.model_validate(f) for f in flags]
    evidence = {k: [EvidenceChunk.model_validate(x) for x in v] for k, v in evidence.items()}
    verdicts = []
    for a in case_file["result"]["attempts"]:
        if a.get("error"):
            verdicts.append(None)
            continue
        from drug_safety.llm.prompts import evidence_labels, resolve_labels

        text = resolve_labels(a["answer"], evidence_labels(flags, evidence))
        # quotes are required from explain-v3 on; earlier runs are judged without them
        old = case_file["result"].get("prompt_version") in ("explain-v1", "explain-v2")
        _, issues = validate(text, flags, evidence, summary, finder, require_quotes=not old)
        verdicts.append(sorted({i.rule for i in issues}))
    return {"first_ok": verdicts[0] == [], "any_ok": any(v == [] for v in verdicts)}


def llm_runs(s: Settings, finder) -> list[dict]:
    from drug_safety.llm.prompts import PROMPT_VERSION

    out = []
    for summary_path in sorted(glob.glob(str(s.outputs_dir / "*_llm_eval" / "*" / "summary.json"))):
        d = Path(summary_path).parent
        summary = json.loads(Path(summary_path).read_text())
        cases = [json.loads(p.read_text()) for p in sorted(d.glob("p*.json"))]
        judged = [j for c in cases if (j := rejudge(c, finder)) is not None]
        out.append({
            "run": f"{d.parent.name}/{d.name}",
            "prompt_version": summary.get("prompt_version"),
            "current_prompt": summary.get("prompt_version") == PROMPT_VERSION,
            "guided": summary.get("guided_json"),
            "cases": summary["cases"],
            "accepted_first_try": summary["accepted_first_try"],
            "accepted_after_retry": summary["accepted_after_retry"],
            "fallback": summary["fallback"],
            "server_errors": summary["server_errors"],
            "issues_by_rule": summary["first_try_issues_by_rule"],
            "seconds_per_patient": summary["seconds_per_patient_mean"],
            "seconds_per_call": summary["seconds_per_call"],
            "rejudged": len(judged),
            "rejudged_first_ok": sum(j["first_ok"] for j in judged),
            "rejudged_any_ok": sum(j["any_ok"] for j in judged),
        })  # fmt: skip
    return out


# ---------------------------------------------------------------- 3. latency


def latency(s: Settings, embedder, repeats: int = 3) -> dict:
    """Retrieval + context per patient (the 10 LLM test patients), PostgreSQL, warm model."""
    from drug_safety.db import connect
    from drug_safety.llm.eval_llm import frozen_path, load_frozen
    from drug_safety.rag.chunking import bge_token_counter
    from drug_safety.rag.retriever import PgRepository, build_context, retrieve

    count = bge_token_counter(s.embedding_model)
    cases = load_frozen(frozen_path(s))
    per_patient = []
    with connect() as conn:
        repo = PgRepository(conn)
        retrieve(cases[0]["flags"][0], repo, embedder)  # warm-up: first query, DB connection
        for c in cases:
            runs = []
            for _ in range(repeats):
                t = time.perf_counter()
                found = {f.flag_id: retrieve(f, repo, embedder) for f in c["flags"]}
                build_context(found, s.llm_context_tokens, count)
                runs.append(time.perf_counter() - t)
            per_patient.append(statistics.median(runs))
    return {
        "patients": len(per_patient),
        "mean_s": round(statistics.mean(per_patient), 3),
        "max_s": round(max(per_patient), 3),
    }


# ---------------------------------------------------------------- 4. definition of done


def papers_loaded() -> int:
    from drug_safety.db import connect

    with connect() as conn:
        return conn.execute(
            "SELECT count(DISTINCT d.doc_id) FROM kb.documents d JOIN kb.evidence_chunks c "
            "USING (doc_id) WHERE d.source = 'pmc'"
        ).fetchone()[0]


def module_docs(base: Path = Path("src/drug_safety")) -> dict[str, list[str]]:
    """What each module README lacks of the spec's handover list (README, environment
    variables, tests). Empty list = complete."""
    out = {}
    for m in MODULE_TESTS:
        p = base / m / "README.md"
        text = p.read_text().lower() if p.exists() else ""
        need = {"README": bool(text), "environment variables": "# environment variables" in text
                or "## environment variables" in text, "tests": "test" in text}  # fmt: skip
        out[m] = [k for k, ok in need.items() if not ok]
    return out


def module_tests() -> dict[str, str]:
    """Each module's tests run alone (spec definition of done, item 7)."""
    out = {}
    for m in MODULE_TESTS:
        p = subprocess.run([sys.executable, "-m", "pytest", f"tests/{m}", "-q",
                            "-o", "addopts=--import-mode=importlib"],
                           capture_output=True, text=True)  # fmt: skip
        last = (p.stdout.strip().splitlines() or ["no output"])[-1]
        out[m] = ("PASS " if p.returncode == 0 else "FAIL ") + last
    return out


def review_status(runs: list[dict], review_text: str) -> tuple[str, str]:
    """Item 5: has the latest run with the current prompt been read (docs/llm_manual_review.md
    has a "## Run N: <its date>" section), and has a person confirmed that reading?"""
    current = sorted(r["run"].split("_llm_eval")[0] for r in runs if r["current_prompt"])
    if not current:
        return ("PENDING", "Needs a Colab run with the current prompt, then the manual review "
                "(docs/llm_manual_review.md)")  # fmt: skip
    day = current[-1]
    section = next((s for s in review_text.split("\n## ")[1:] if day in s.splitlines()[0]), None)
    if section is None:
        return ("CHECK", f"Run {day} not yet reviewed: read its review.md and record it in "
                "docs/llm_manual_review.md")  # fmt: skip
    if "**Confirmed by a person:** ☐" in section:
        return ("REVIEWED, TO CONFIRM", f"Run {day} read (docs/llm_manual_review.md): no "
                "invented drug, dose or number; wrong or unsupported statements listed there. "
                "A person still has to confirm the reading")  # fmt: skip
    return ("REVIEWED", f"Run {day} read and confirmed (docs/llm_manual_review.md)")


def definition_of_done(
    ret: dict, runs: list[dict], papers: int, tests: dict, docs: dict, review_text: str = ""
) -> list[tuple]:
    """(item, status, detail). Person B's items of the spec's definition of done."""
    item5 = review_status(runs, review_text)
    rows = [
        ("2. At least 500 papers in documents, chunked and embedded",
         "PASS" if papers >= PAPERS_REQUIRED else "ACCEPTED DEVIATION",
         f"{papers} papers chunked and embedded; fewer than 500 accepted by Person A on "
         "2026-11-14 (question F1)"),
        ("4. For every gold flag, the expected guideline or label chunk is in the top 3",
         "PASS" if ret["hit"] == ret["cases"] and not ret["failures"] else "FAIL",
         f"{ret['hit']}/{ret['cases']} (label {ret['label_hit']}/{ret['cases']}, CPIC "
         f"{ret['cpic_hit']}/{ret['gene_flags']} gene flags). Gold flags are B's "
         f"{ret['cases']} hand-written "
         "engine-style flags until Person A's gold set exists"),
        ("5. Validated LLM output adds no drug, interaction or suggestion beyond the flags and "
         "evidence",
         *item5),
        ("6. One patient JSON goes through the API and the Streamlit screen to a finished report",
         "PENDING",
         "Works through the stand-in API (make verify-ui, 10/10). Waits for Person A's API"),
        ("7. Every module has a README, an environment-variable list and tests that run alone",
         "PASS" if all(v.startswith("PASS") for v in tests.values())
         and not any(docs.values()) else "FAIL",
         "; ".join(f"{m}: {v[5:]}" + (f" (README lacks {', '.join(docs[m])})" if docs[m] else "")
                   for m, v in tests.items())),
        ("7. docker compose up starts the stack", "PENDING",
         "B's ui image is built and checked (docker/ui.Dockerfile); its compose entry is a "
         "proposal in docker/compose.ui.yml. The compose file is Person A's"),
    ]  # fmt: skip
    return rows


# ---------------------------------------------------------------- report


def render(ret: dict, runs: list[dict], lat: dict, dod: list[tuple]) -> str:
    L = [f"# Evaluation report: Person B ({date.today():%Y-%m-%d})", "",
         "Spec: \"Evaluation of the full pipeline\" and \"Definition of done\". Flag recall and "
         "precision belong to the rule engine (Person A).", ""]  # fmt: skip

    L += ["## 1. Retrieval hit rate", "",
          f"**{ret['hit']}/{ret['cases']}** gold flags have their expected guideline or label "
          "chunk in the top 3.", "",
          "| Flag type | Hit |", "|---|---|"]  # fmt: skip
    L += [f"| {t} | {h}/{n} |" for t, (h, n) in sorted(ret["by_type"].items())]
    L += ["", f"Expected label passage: {ret['label_hit']}/{ret['cases']}. CPIC recommendation "
          f"for the flag's phenotype: {ret['cpic_hit']}/{ret['gene_flags']} gene flags (warfarin "
          f"has none in CPIC). Flags with a supporting paper: {ret['with_paper']}."]  # fmt: skip
    if ret["failures"]:
        L += ["", "Failures: " + "; ".join(ret["failures"])]

    L += ["", "## 2. LLM faithfulness and fallback rate", ""]
    if not runs:
        L += ["No Colab run found in data/outputs/*_llm_eval/."]
    else:
        L += ["| Run | Prompt | Mode | Accepted 1st try | After retry | Template (fallback) | "
              "Re-judged by today's validator: 1st try / by retry |",
              "|---|---|---|---|---|---|---|"]  # fmt: skip
        for r in runs:
            stale = "" if r["current_prompt"] else " (older prompt)"
            n, j = r["cases"], r["rejudged"]
            mode = "guided" if r["guided"] else "plain"
            L.append(
                f"| {r['run']} | {r['prompt_version']}{stale} | {mode} | "
                f"{r['accepted_first_try']}/{n} | {r['accepted_after_retry']} | {r['fallback']}/{n}"
                f" | {r['rejudged_first_ok']}/{j} / {r['rejudged_any_ok']}/{j} |"
            )
        if not any(r["current_prompt"] for r in runs):
            L += ["", "**No run with the current prompt yet**: the next Colab run gives the "
                  "numbers that count."]  # fmt: skip
    L += ["", "Manual check: [docs/llm_manual_review.md](../../../docs/llm_manual_review.md)."]

    L += ["", "## 3. Latency per patient", "",
          f"- **Without the LLM** (retrieval + context, PostgreSQL, {lat['patients']} patients, "
          f"median of 3, warm): mean **{lat['mean_s']} s**, max {lat['max_s']} s. Loading the "
          f"embedding model once at start-up: {lat['model_load_s']} s."]  # fmt: skip
    # the latest run in the mode the app uses (guided JSON by default since run 2)
    default_mode = [r for r in runs if r["current_prompt"] and r["guided"]]
    latest = (default_mode or runs)[-1] if runs else None
    if latest:
        llm_s = latest["seconds_per_patient"]
        total = round(lat["mean_s"] + llm_s, 1)
        L.append(f"- **With the LLM**: + {llm_s:.1f} s model time per patient ({latest['run']}, "
                 f"{latest['prompt_version']}) = about **{total} s**.")  # fmt: skip

    L += ["", "## 4. Definition of done (Person B's items)", "", "| Item | Status | Detail |",
          "|---|---|---|"]  # fmt: skip
    L += [f"| {i} | **{st}** | {d} |" for i, st, d in dod]
    return "\n".join(L) + "\n"


def main() -> int:
    from drug_safety.llm.drug_names import DrugNameFinder
    from drug_safety.rag.embeddings import Embedder

    s = get_settings()
    t = time.perf_counter()
    embedder = Embedder(s.embedding_model, s.cache_dir)
    embedder.embed_query("warm-up")
    model_load = round(time.perf_counter() - t, 1)
    ret = retrieval(s, embedder)
    runs = llm_runs(s, DrugNameFinder.load(s))
    lat = latency(s, embedder) | {"model_load_s": model_load}
    review = MANUAL_REVIEW.read_text(encoding="utf-8") if MANUAL_REVIEW.exists() else ""
    dod = definition_of_done(ret, runs, papers_loaded(), module_tests(), module_docs(), review)
    out = s.outputs_dir / f"{date.today():%Y-%m-%d}_evaluation"
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(render(ret, runs, lat, dod), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(
        {"retrieval": ret, "llm_runs": runs, "latency": lat,
         "definition_of_done": [dict(zip(("item", "status", "detail"), r, strict=True))
                                for r in dod]}, indent=1))  # fmt: skip
    print((out / "report.md").read_text())
    print(f"Written to {out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
