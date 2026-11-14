# llm

**Owner:** Person B · **Status:** built; first real Qwen run done on Colab (2026-11-11, prompt
`explain-v1`); fixes from it made and checked offline (`explain-v2`); **re-run on Colab pending**
([notebooks/colab_qwen_vllm.ipynb](../../../notebooks/colab_qwen_vllm.ipynb))

## What it does

Turns the rule engine's flags and their retrieved evidence into the spec's `Report` (p16):
a plain-language summary, one explanation per flag, and "Consider reviewing ..." suggestions,
every one citing evidence IDs. **It explains; it never decides what is unsafe.**

```
flags + evidence ─► prompt ─► Qwen ─► validator ─┬─ pass ─────────────► Report (source "llm")
                                 ▲               └─ fail ─► retry once with the error list
                                 └──────────────────────────┘   fail again / server down
                                                                 └─► template Report (source "template")
                                                                     + notice "AI summary unavailable"
```

| File | Job |
|---|---|
| [`prompts.py`](prompts.py) | System prompt (the spec's 6 prompt rules + schema), patient summary (no patient ID), each flag with only its own evidence, retry message |
| [`validator.py`](validator.py) | Accept or reject an answer: spec rules 1–6 plus rule 7 (numbers) |
| [`drug_names.py`](drug_names.py) | Finds drug names in text, for rule 4 |
| [`fallback.py`](fallback.py) | Template report from the flags alone; passes the validator itself |
| [`client.py`](client.py) | OpenAI-compatible client (`MODEL_URL`, `MODEL_NAME`, `MODEL_API_KEY`), temperature 0.1, `ping` |
| [`explain.py`](explain.py) | The loop above (`explain`), and `explain_patient` = retrieve → trim → explain, for Person A's API |
| [`eval_llm.py`](eval_llm.py) | The evaluation on 10 synthetic patients (build / verify / run / bundle) |

## Use from the API (Person A)

```python
from drug_safety.llm.client import chat_model
from drug_safety.llm.drug_names import DrugNameFinder
from drug_safety.llm.explain import explain_patient
from drug_safety.rag.chunking import bge_token_counter
from drug_safety.rag.retriever import PgRepository

evidence, result = explain_patient(
    patient,
    flags,
    PgRepository(conn),
    embedder,
    chat_model(settings),
    DrugNameFinder.load(settings),
    bge_token_counter(settings.embedding_model),
    settings.llm_context_tokens,
)
# AssessResponse(..., evidence=evidence, report=result.report,
#                report_source=result.report_source, notice=result.notice)
```
Load the finder and the chat model once at start-up, not per request.

## The validator's rules

An answer is accepted only if **all** hold. Each failure becomes a message sent back to Qwen in the retry.

| # | Rule (spec p16) | How it is checked |
|---|---|---|
| 1 | Parses into `Report` | JSON (a markdown fence around it is tolerated), then the contract model (unknown fields rejected) |
| 2 | Every `flag_id` exists; every flag has an explanation | Unknown IDs, missing flags and duplicate explanations are all rejected |
| 3 | Every evidence ID was supplied **for that flag** | Per flag, not "any supplied ID". An explanation of a flag that has evidence must cite some; a flag with no evidence must be named under `uncertainties` |
| 4 | No drug outside the prescriptions and flags | Every prescribable RxNorm ingredient and brand (about 9,000 names), plus the v1 aliases, is looked for. Allowed: flag drugs, interacting drugs, and anything named in the patient summary's prescriptions |
| 5 | Every suggestion cites evidence | Checked before the schema, so the error message says so plainly |
| 6 | No instruction to stop a drug or change a dose | In every field: orders ("Stop X"), modal instructions ("should be discontinued", "dose should be reduced"), and a dose change stated as needed ("dose adjustments are necessary", "a need for dose adjustment", "consider reducing the dosage"). Suggestions must start "Consider reviewing / monitoring / checking / assessing / evaluating / discussing" and may not suggest stopping, switching or changing a dose. Describing the evidence ("the label recommends a lower starting dose", "may require a lower dose") is allowed |
| 7 | **Added by B**: no number not in the flags, evidence or patient summary | From the spec's prompt rule "never add a dose or number"; counted separately in the evaluation so its cost can be seen |

**What it does not catch:** a wrong claim using only allowed words and numbers (e.g. stating the
wrong direction of an effect). That is what the manual review in `review.md` is for (spec:
"a manual check of about 10 reports").

### Drug names (rule 4): where the list comes from
`make fetch-drug-names` downloads NLM's Prescribable RxNorm ingredient and brand lists. Names
that are ordinary words, lab analytes or body chemistry ("water", "glucose", "creatinine",
"protein C", "IBU") are removed by [`scope/drug_name_ignore.yaml`](../../../scope/drug_name_ignore.yaml).
That list was built from dictionary collisions, then **checked by running the finder over all
8,549 evidence chunks and reading all 444 names it reported**. Real medicines that are also
dictionary words (morphine, heparin, lithium) are still detected.

Strictness to know about: a drug that appears **only in the evidence** (e.g. prasugrel in the
CPIC clopidogrel text) is still rejected, as the spec says. Qwen must say "an alternative
antiplatelet", as the spec's p19 example does.

## First real run (Colab, 2026-11-11)

Qwen 2.5 7B Instruct, full precision, vLLM 0.33.0, NVIDIA L4, prompt `explain-v1`, plain mode,
10 synthetic patients. Results: `data/outputs/2026-11-11_llm_eval/plain/`. Every answer was read
by hand.

| | Result |
|---|---|
| Accepted first try / after retry / template | 5 / 2 / 3 of 10 |
| Invented drugs (rule 4) or numbers (rule 7) | **0** of 15 answers |
| Wrong claims about the evidence | None found |
| Seconds per answer | 55 mean, 83 max |

**What it showed, and what changed (`explain-v2`):**

| Found | Fix | Checked by |
|---|---|---|
| Dose instructions in explanations passed: "Dose adjustments are necessary", "a need for dose adjustment" (p09 was accepted, wrongly) | Rule 6 covers a dose change stated as needed, in every field | `test_real_answers.py`: p09 now rejected |
| "Consider alternative antiplatelet therapy…" passed (it only had to start "Consider") | Suggestions must start "Consider" plus a review verb | p01's second answer now rejected |
| "Consider reviewing … potential dosage adjustments" and "… may require a lower dose" were rejected, causing 2 needless fallbacks | A dose change counts in a suggestion only as the action ("adjusting the dosage"), not as what to review | p02 and p06 second answers now accepted |
| Qwen left out a flag on 4 of 10 first tries | The prompt ends with the exact list of flag IDs to explain | Next Colab run |
| The retry repeated rejected wording word for word | Error messages quote the problem and say how to rewrite it | Next Colab run |
| A third of each answer was copied chunk IDs | Evidence shown as `E1`, `E2`…; mapped back to chunk IDs before validation | Answers 54% fewer tokens (measured on the 15 real answers) |

Re-judged by the new validator, the same 15 answers give 4/10 accepted first try and 7/10 by the
retry. The next Colab run measures the new prompt itself.

## Run

| Command | Needs | What it does |
|---|---|---|
| `make fetch-drug-names` | Network (2 requests) | Drug name list for rule 4 |
| `make llm-cases` | PostgreSQL | Retrieves and freezes the evidence for the 10 test patients ([tests/gold/llm_cases.yaml](../../../tests/gold/llm_cases.yaml): all 26 gold flags; patient 1 = spec worked example) |
| `make verify-llm` | Nothing (downloads Qwen's tokenizer once) | Offline checks + `pytest tests/llm` |
| `make llm-bundle` | — | `data/outputs/llm_bundle.zip` for Colab |
| `make llm-ping` | The model server | Is `MODEL_URL` up and serving `MODEL_NAME`? |
| `make eval-llm` | The model server | The evaluation, plain then schema-guided |

### With Google Colab (the GPU for now)
1. `make llm-cases verify-llm llm-bundle` on the Mac.
2. Open `notebooks/colab_qwen_vllm.ipynb` in Colab with a GPU runtime and run cells 1–4: they
   start vLLM with Qwen 2.5 7B (AWQ 4-bit on a T4, full precision on L4/A100), an API key and
   a 16,384-token window.
3. **Part A (recommended):** upload the bundle, run the evaluation in Colab, download the results zip,
   and unzip it in the repo folder.
   **Part B (optional):** a temporary HTTPS tunnel. Put the printed `MODEL_URL`, `MODEL_NAME` and
   `MODEL_API_KEY` in `.env`, then `make llm-ping` and `make eval-llm` from the Mac.
4. **Synthetic patients only**: Colab is Google's machine.

## Health check
`make llm-ping` → `OK   <model> served at <url>`. In the app, `client.ping(settings)` returns
`(ok, detail)` for the API's `GET /health`.

## How to know it worked (no code review needed)

`make verify-llm` must end in `RESULT: PASS` and `62 passed`. It checks:

| Check | Why it matters |
|---|---|
| The frozen cases match the 10 patients; every flag has evidence; evidence within budget | Qwen gets what retrieval found |
| The prompt fits Qwen's window **including the retry** (worst 6,928 tokens + answer) | No silent truncation |
| The template fallback passes the validator for all 10 patients | The safe path is itself safe |
| Tests: the spec's own p19 example passes; a correct answer passes | The validator is not too strict |
| Tests: 18 broken answers, each breaking one rule, are each rejected by **that** rule | The validator is not too loose |
| Tests: 10 normal descriptions ("Lower kidney function…", "smoking cessation") are not taken as orders | False alarms would cause needless fallbacks |
| Tests: accept / retry-with-errors / two failures → template / server down → template | The spec's loop |
| Tests: the 15 real Qwen answers from the first run each get the verdict a reviewer gave them | The validator is right on real output, both ways |
| Tests: evidence labels map back to chunk IDs; retry messages use labels; chunk IDs never reach the model | The label change is safe |

After a model run (`data/outputs/<date>_llm_eval/<label>/`):
- `summary.json`: `faithfulness_first_try` (spec: share accepted on the first try),
  `fallback_rate`, `first_try_issues_by_rule`, seconds per call and per patient.
- `review.md`: each report beside its evidence, with a box per flag for the manual check.
- One file per patient. Since step 10 it also holds the flags, evidence and summary that were
  sent, so `make eval-report` can re-judge an old run with today's validator.
- Record the manual check in [docs/llm_manual_review.md](../../../docs/llm_manual_review.md).

## Environment variables
`MODEL_URL`, `MODEL_NAME`, `MODEL_API_KEY` (default `local`), `MODEL_GUIDED_JSON` (false),
`MODEL_MAX_TOKENS` (1500), `MODEL_TIMEOUT_S` (180), `LLM_CONTEXT_TOKENS` (3500 BGE tokens of
evidence per patient).

## Found while checking
1. **The retry didn't fit an 8,192-token window.** Worst case (p09): 5,028 prompt + failed answer + errors
   + new answer ≈ 8,400 tokens. vLLM is started with 16,384 instead (Qwen 2.5 supports 32k), rather
   than cutting evidence.
2. **The template report failed rule 6**: the engine reason "CYP2C9 poor metabolizer: lower warfarin dose
   requirement" was split at the colon, making "lower warfarin dose" look like an order. Colons now
   split only before a capital ("Note: Stop it." is still caught). A test covers both.
3. **The whole RxNorm list flagged ordinary words** (623 dictionary collisions, e.g. "air", "lead"). Switched to
   the prescribable subset, and reviewed what was left (above).
4. A settings field named `model_json_schema` silently shadowed a pydantic method; renamed
   `model_guided_json`.
5. Five deliberate breaks to the validator (rule 4 off, rule 3 across flags, rule 7 off, "Consider" off,
   missing-explanation off) were each caught by a test; so were five breaks to the `explain-v2`
   changes (labels not resolved, retry showing chunk IDs, dose-needed check off, old "Consider"
   start, flag list removed).
6. The first real run found the validator too loose in two places and too strict in one (above,
   "First real run"); fixed and pinned by `tests/llm/test_real_answers.py`.

## Tests
`pytest tests/llm` (offline; the drug-list test skips if `make fetch-drug-names` hasn't been run).
