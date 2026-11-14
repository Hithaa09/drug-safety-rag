"""The evaluation report's logic (offline: no database, no model)."""

import json
from pathlib import Path

import yaml

from drug_safety.evaluation.report import (
    MODULE_TESTS,
    definition_of_done,
    module_docs,
    rejudge,
    render,
)
from drug_safety.llm.drug_names import ALIASES_FILE, DrugNameFinder

FIXTURE = Path("tests/llm/fixtures/qwen_2026-11-11_plain.json")


def finder():
    names = {n: n for n in ["prasugrel", "ticagrelor", "esomeprazole", "nexium", "morphine"]}
    for drug, aliases in yaml.safe_load(ALIASES_FILE.read_text())["aliases"].items():
        names |= {a.lower(): drug for a in [drug, *aliases]}
    return DrugNameFinder(names)


RET = {"hit": 26, "cases": 26, "label_hit": 25, "cpic_hit": 8, "gene_flags": 9,
       "by_type": {"gene_drug": [9, 9]}, "with_paper": 15,
       "failures": [], "misses": []}  # fmt: skip
TESTS_OK = {m: "PASS 1 passed" for m in MODULE_TESTS}
DOCS_OK = {m: [] for m in MODULE_TESTS}


def status(rows, prefix):
    return next(st for item, st, _ in rows if item.startswith(prefix))


def test_definition_of_done_statuses():
    rows = definition_of_done(RET, [], 344, TESTS_OK, DOCS_OK)
    assert status(rows, "2.") == "ACCEPTED DEVIATION"  # 344 < 500, accepted by A (F1)
    assert status(rows, "4.") == "PASS"
    assert status(rows, "5.") == "PENDING"  # no run with the current prompt
    assert status(rows, "7. Every") == "PASS"
    assert (
        status(definition_of_done({**RET, "hit": 25}, [], 500, TESTS_OK, DOCS_OK), "4.") == "FAIL"
    )
    assert status(definition_of_done(RET, [], 500, TESTS_OK, DOCS_OK), "2.") == "PASS"
    broken = {**TESTS_OK, "rag": "FAIL 1 failed"}
    assert status(definition_of_done(RET, [], 344, broken, DOCS_OK), "7. Every") == "FAIL"
    no_env = {**DOCS_OK, "rag": ["environment variables"]}
    rows = definition_of_done(RET, [], 344, TESTS_OK, no_env)
    assert status(rows, "7. Every") == "FAIL"
    assert "README lacks environment variables" in next(
        d for i, _, d in rows if i.startswith("7. E")
    )


def test_module_docs_finds_a_missing_section(tmp_path):
    for m in MODULE_TESTS:
        (tmp_path / m).mkdir()
        (tmp_path / m / "README.md").write_text(
            "# x\n\n## Environment variables\nNone.\n## Tests\n"
        )
    assert all(v == [] for v in module_docs(tmp_path).values())
    (tmp_path / "rag" / "README.md").write_text("# rag\n## Tests\n")
    (tmp_path / "ui" / "README.md").unlink()
    got = module_docs(tmp_path)
    assert got["rag"] == ["environment variables"] and "README" in got["ui"]


def test_rejudge_finds_run_1_inputs_in_the_fixture():
    c = json.loads(FIXTURE.read_text())["cases"][8]  # p09: accepted by v1, rejected now
    case_file = {"case": c["case"], "result": {"attempts": [{"answer": a} for a in c["answers"]]}}
    assert rejudge(case_file, finder()) == {"first_ok": False, "any_ok": False}
    case_file["result"]["attempts"][0]["answer"] += " "  # not the same answers: no inputs
    assert rejudge(case_file, finder()) is None


def test_render_without_runs_says_so():
    lat = {"patients": 10, "mean_s": 0.09, "max_s": 0.12, "model_load_s": 12.0}
    text = render(RET, [], lat, definition_of_done(RET, [], 344, TESTS_OK, DOCS_OK))
    assert "No Colab run found" in text and "**26/26**" in text and "**ACCEPTED DEVIATION**" in text
