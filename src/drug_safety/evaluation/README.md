# evaluation

**Owner:** Person B · **Status:** built (2026-11-11). The LLM numbers that count wait for a
Colab run with the current prompt.

## What it does
Writes Person B's evaluation report, as the spec defines it ("Evaluation of the full pipeline"
and "Definition of done"), from what is already on disk and in PostgreSQL. It never calls the
model.

| Section | Measured how |
|---|---|
| Retrieval hit rate | The 26 gold flags through `retrieve()` and PostgreSQL (`rag/eval_flags.py`): is the expected guideline or label chunk in the top 3? Split by flag type, label vs CPIC |
| LLM faithfulness, fallback rate | Every Colab run in `data/outputs/*_llm_eval/`. Each answer is also **re-judged by today's validator**, so an old run is read with today's rules. Runs on an older prompt are marked |
| Latency per patient | Without the LLM: retrieval + context for the 10 test patients, timed here (warm, median of 3), plus the one-off model load. With the LLM: that plus the model time from the latest run |
| Definition of done (B's items 2, 4, 5, 6, 7) | PASS, FAIL (with the reason) or PENDING (what it waits for). Item 7 runs each module's tests on their own and checks each README has an environment-variable section |

Flag recall and precision are the rule engine's (Person A).

## Run
```bash
make eval-report   # needs PostgreSQL; about 1.5 min (runs every module's tests)
```
Writes `data/outputs/<date>_evaluation/report.md` and `metrics.json`.

The manual check of LLM reports (spec: "about 10 reports") is recorded by hand in
[docs/llm_manual_review.md](../../../docs/llm_manual_review.md).

## Environment variables
`DATABASE_URL`, `EMBEDDING_MODEL`, `DATA_DIR`, `LLM_CONTEXT_TOKENS` (as in the rag and llm modules).

## Tests
`pytest tests/evaluation` (offline):
- the definition-of-done statuses (344 papers fails; one missing hit fails; a module whose tests
  fail, or whose README lacks the environment section, fails);
- the README check, against made-up READMEs;
- re-judging run 1 through its saved fixture (p09 is rejected today), and refusing when the
  answers don't match;
- the report states when there is no Colab run.
