# Drug Safety RAG: Person B's modules

This repository is Person B's half of the Drug Safety RAG project. It covers:
- the evidence: FDA labels, CPIC and DPWG guidelines, and research papers, chunked, embedded and stored in PostgreSQL;
- the retriever: a flag in, cited evidence out;
- the LLM layer: Qwen 2.5 7B via vLLM, with a validator and a safe template fallback;
- the Streamlit screen;
- the evaluation.

Person A owns the contracts' consumers, the warehouse, the rule engine and the API. Questions waiting for Person A are in
[docs/questions_for_person_a.md](docs/questions_for_person_a.md).

> **Rules decide what is unsafe (Person A). RAG finds the evidence, and Qwen only explains it (Person B).**
> The output is decision support for a pharmacist or clinician, not medical advice.

State on **2026-11-14**: every module is built and checked, and `make check` passes 369 tests.
- **Still to come:** the rule engine and API (Person A), and the third Colab run of the model.
- **Open:** 13 questions for Person A, 6 of them blocking (⭐).

---

## Contents
1. [Status at a glance](#1-status-at-a-glance)
2. [Test and evaluation results](#2-test-and-evaluation-results)
3. [How the pieces fit](#3-how-the-pieces-fit)
4. [Getting started from a clean clone](#4-getting-started-from-a-clean-clone)
5. [Running the AI model (Colab for now)](#5-running-the-ai-model-colab-for-now)
6. [Integrating into the main repo (for Person A)](#6-integrating-into-the-main-repo-for-person-a)
7. [Known faults and limitations](#7-known-faults-and-limitations)
8. [Open questions for Person A](#8-open-questions-for-person-a)
9. [Sources and licences](#9-sources-and-licences)
10. [Repository layout](#10-repository-layout)
11. [Decisions so far](#11-decisions-so-far)
12. [Where to read more](#12-where-to-read-more)

---

## 1. Status at a glance

| Module | What it does | State | Key number | Check it yourself |
|---|---|---|---|---|
| `contracts` | Shared models: `Patient`, `Flag`, `Report`, `AssessResponse` (version 0.1.0) | Agreed with Person A on 2026-11-14 | 20 tests | `pytest tests/contracts` |
| `ingest` | Loaders: DailyMed labels, openFDA IDs, RxClass, PubMed/PMC papers, CPIC, DPWG (ClinPGx), French BDPM label, RxNorm names | Done | 24 of 25 drugs have a US label (acenocoumarol has none: BDPM below) | `make verify-dailymed verify-openfda verify-classes verify-papers` |
| `rag`: labels | Topic-based chunking (labels-v3) → bge-base embeddings → `kb.evidence_chunks` | Done | 1,519 chunks from 24 labels | `make verify-chunks verify-store` |
| `rag`: papers | 445 papers selected, 344 chunked (licence allows full text) | Done (fewer than the spec's 500; accepted, F1) | 6,933 chunks | `make verify-paper-chunks` |
| `rag`: guidelines | CPIC (CC0) plus DPWG for pairs CPIC lacks | Done | 184 CPIC recommendations (9 guidelines), 4 DPWG | `make verify-cpic verify-dpwg` |
| `rag`: acenocoumarol | French label, English translation | **Waiting for a French reader** (E7) | 13 passages translated, 0 reviewed, so 0 chunks | `make verify-bdpm` |
| `rag`: retriever | `Flag` → 2 tier-1 passages + 1 close paper, with rules for guidelines, own label and lifestyle factors | Done | 28/28 gold flags | `make eval-flags` |
| `curation` | 37 lab and lifestyle rules drafted from label text, each with verbatim quotes | **Drafts: waiting for review** (C1) | 682/682 checks | `make verify-curation`; sheet `docs/curation_review.md` |
| `llm` | Prompt (`explain-v3`), Qwen via vLLM, validator (8 rules), one retry, template fallback | Built; **Colab run 3 pending** | Runs 1 and 2 done (below) | `make verify-llm` |
| `ui` | Streamlit screen: patient form or JSON upload → flags, evidence, report | Built; checked against a stand-in API | 10/10 example patients | `make verify-ui`; try it: `make stub-api` + `make ui` |
| `evaluation` | Retrieval hit rate, faithfulness, fallback rate, latency, definition of done | Built | Report in `data/outputs/<date>_evaluation/` | `make eval-report` |

Every module folder has its own README: inputs, outputs, how to run it, how to verify it, environment variables, troubleshooting.

---

## 2. Test and evaluation results

All results are from 2026-11-14, on a Mac with Python 3.14 and a local PostgreSQL 16 with pgvector.

### Automated tests: `make check` (lint clean, **369 passed**)

363 run offline; 6 (`tests/rag/test_store_db.py`) need the local database and are skipped when it is not running (`make db`).

| Module | Tests | Module | Tests |
|---|---|---|---|
| contracts | 20 | llm | 100 |
| ingest | 76 | ui | 65 |
| rag | 79 | curation | 16 |
| evaluation | 5 | setup | 8 |

Tests include regression tests on **real Qwen answers** from both Colab runs
(`tests/llm/test_real_answers*.py`). Each answer has a human-checked verdict, so the validator
is held to real model output in both directions.

### Verify commands (each ends in `RESULT: PASS`)
`verify-dailymed`, `verify-chunks`, `verify-openfda`, `verify-classes`, `verify-store`,
`verify-papers`, `verify-paper-chunks`, `verify-cpic`, `verify-dpwg`, `verify-bdpm`,
`verify-curation`, `verify-llm`, `verify-ui`.

### Retrieval (spec: "for every gold flag, the expected chunk is in the top 3")

| Measure | Result |
|---|---|
| Gold flags with the expected guideline or label chunk in the top 3 (`make eval-flags`) | **28/28** |
| … with the expected **label** passage | 28/28 (27/28 before the topic-based chunking) |
| … gene flags with the CPIC recommendation for **their own phenotype** | 11/11 (warfarin has no CPIC table; DPWG covers it) |
| Gold label cases, both embedding models (`make eval-retrieval`) | 28/28; MRR 0.798 and 0.801 (0.692 and 0.718 before) |
| Same results through SQL as in memory (`make verify-store`) | Yes |

⚠ **This is self-graded.** The 28 gold flags were written by Person B, in the engine's format.
It must be redone on real engine flags (H6, F4).

### LLM (10 synthetic patients, Qwen 2.5 7B Instruct, vLLM 0.33.0, NVIDIA L4 on Colab)

| Run | Prompt | Mode | Accepted 1st try | After retry | Template fallback | Wrong statements found by reading |
|---|---|---|---|---|---|---|
| 1 (2026-11-11) | explain-v1 | plain | 5/10 | 2 | 3/10 | None found in 15 answers |
| 2 (2026-11-14) | explain-v2 | **guided JSON** | 3/10 | 7 | **0/10** | 3 (p02, p06, p08) |
| 2 (2026-11-14) | explain-v2 | plain | 3/10 | 4 | 3/10 | 2 more (p02, p04) |
| 3 | **explain-v3** (quotes, rule 8) | guided | **pending** | | | |

- **Invented drugs, doses or numbers in any accepted answer: 0.**
- Seconds per model call: 55 in run 1, 27 in run 2. Evidence is now cited as `E1`, `E2` instead of 60-character IDs.
- **Guided JSON is the default.** It produced no broken JSON and half the fallbacks of plain mode.
- Re-judged by today's stricter validator, run 2 guided is 2/10 first try, 7/10 by the retry.
- Full reading of every report: [docs/llm_manual_review.md](docs/llm_manual_review.md). It still needs a person's confirmation (H7).

### Latency per patient
- **Without the LLM** (retrieval and context, PostgreSQL, warm): mean 0.09 s, max 0.14 s. The embedding model takes about 14 s to load once at start-up.
- **With the LLM** (Colab L4): about 46 s, which is two calls when the first answer is retried.

### Definition of done (Person B's items, from `make eval-report`)

| Item | Status |
|---|---|
| 2. At least 500 papers | Accepted deviation: 344 (F1) |
| 4. Every gold flag's expected chunk is in the top 3 | PASS (self-graded, see above) |
| 5. Validated LLM output adds nothing beyond flags and evidence | PENDING: Colab run 3 with explain-v3, then a person's review |
| 6. One patient through the API and the screen | PENDING: works through the stand-in API; waits for Person A's API |
| 7. Every module has a README, an environment-variable list and tests | PASS |
| 7. `docker compose up` starts the stack | PENDING: the `ui` image is ready; the compose file is Person A's |

---

## 3. How the pieces fit

```
Patient JSON ──► [Person A] normaliser + rule engine ──► Flags (contract: Flag)
                                                            │
                         [B] rag.retriever: per flag, filter by drug, rank by vector distance,
                             take 2 tier-1 passages (label / CPIC / DPWG) + 1 close paper
                                                            │
                         [B] rag.build_context: fit all flags' evidence into a token budget
                                                            │
                         [B] llm.explain_patient: prompt (explain-v3) ─► Qwen on vLLM (MODEL_URL)
                                                            │
                         [B] llm.validator: 8 rules ─► pass: Report
                                                   └─► fail: one retry with the errors,
                                                        then the template report (always safe)
                                                            │
[Person A] POST /v1/assess returns AssessResponse ──► [B] Streamlit screen (ui)
```

**The validator's rules** (details in `src/drug_safety/llm/README.md`):
1. Valid JSON in the `Report` shape.
2. Every flag explained exactly once.
3. Evidence cited only from that flag's own passages.
4. No drug outside the prescriptions and flags.
5. Every suggestion cites evidence.
6. No instruction to stop, switch or change a dose.
7. No number that isn't in the input.
8. Each explanation carries 1–3 quotes copied word for word from its evidence (checked, then dropped).

**The retriever's rules** (details in `src/drug_safety/rag/README.md`):
- A guideline chunk counts only for a gene flag with the same phenotype, at most one per flag.
- The flag drug's own label comes before another drug's label.
- A lifestyle flag's passages must name the factor (smoking, alcohol, grapefruit, dietary vitamin K).
- No papers for lab flags.
- A paper is used only if its distance is at most 0.235.

---

## 4. Getting started from a clean clone

**You need:** Python 3.11+, Docker Desktop (for the local PostgreSQL + pgvector), about 2 GB of
disk, and internet for the first data download. The `data/` folder is **not** in the repo (it is
git-ignored and some sources may not be redistributed); the commands below rebuild it.

```bash
git clone <this repo> && cd drug-safety-rag
make setup                 # .venv, all dependencies, data/ folders, .env from .env.example
make check                 # lint + 369 tests (6 skip until `make db`); should end with "passed"
```

### Rebuild the evidence (in this order; each verify should end in `RESULT: PASS`)

| Step | Commands | Time (about) |
|---|---|---|
| Database | `open -a Docker` (Mac), then `make db && make db-migrate` | 1 min |
| FDA labels | `make ingest-dailymed verify-dailymed fetch-openfda verify-openfda fetch-classes verify-classes` | 3 min |
| Label chunks | `make chunk-labels verify-chunks store-labels` | 3 min (embedding) |
| Papers | `make select-papers fetch-papers parse-papers verify-papers chunk-papers verify-paper-chunks store-papers` | 15–25 min (NCBI at ~1 request/s; resumable) |
| Guidelines | `make fetch-cpic chunk-cpic verify-cpic store-cpic fetch-dpwg chunk-dpwg verify-dpwg store-dpwg` | 2 min |
| French label | `make fetch-bdpm chunk-bdpm verify-bdpm store-bdpm` (0 chunks until the translation is reviewed) | 1 min |
| Checks | `make verify-store eval-retrieval eval-flags verify-curation` | 5 min |
| LLM | `make fetch-drug-names verify-drug-names llm-cases verify-llm` | 2 min |
| Screen | `make verify-ui`, then `make stub-api` and, in a second terminal, `make ui` → http://localhost:8501 | — |
| Report | `make eval-report` | 3 min |

`make help` lists every command. DailyMed labels are pinned per drug in
`scope/dailymed_labels.yaml`. `make check-updates` tells you if a newer label was published,
so a rebuild months later may need a look there first.

**Privacy:** requests to NCBI and other sources use a generic tool name and no personal email. Put an API key in `.env` only if you have one.

---

## 5. Running the AI model (Colab for now)

There is no project GPU server yet, so Qwen runs on Google Colab with **synthetic patients only**
(G3: Colab is Google's machine).

1. `make llm-bundle` writes `data/outputs/llm_bundle.zip`: the code, the frozen evidence for the 10 test patients, and the drug-name list.
2. Open `notebooks/colab_qwen_vllm.ipynb` in Colab and choose an **L4 GPU** (runtime → change runtime type).
3. Run cells 1–4 and wait for `ready after …s`. Loading the model takes about 4 minutes.
4. In cell 5, upload the bundle. Cell 6 runs plain, then guided, and prints a line per patient. It takes 15–30 minutes.
5. Cell 7 downloads `llm_eval_results.zip`. Then disconnect and delete the runtime.
6. Unzip it into `data/outputs/`, run `make eval-report`, and read each `review.md` (the manual check).

After Colab, any machine that can run `vllm serve Qwen/Qwen2.5-7B-Instruct` works. Only
`MODEL_URL`, `MODEL_NAME` and `MODEL_API_KEY` change (G4). The server must support
`response_format: json_schema` while `MODEL_GUIDED_JSON=true` (H5).

---

## 6. Integrating into the main repo (for Person A)

The package path is `src/drug_safety/<module>/`, the same as the main repo, so integration is a
copy with no renaming.

1. **Copy the code:** `src/drug_safety/{ingest,rag,llm,ui,evaluation,curation}` and the matching `tests/` folders.
   - From `ingest/`, take only B's loaders: `dailymed`, `openfda`, `rxclass`, `pubmed_select`, `pmc`, `rxnorm_names`, `cpic`, `dpwg`, `bdpm`.
2. **Copy the scope and curation files:**
   - `scope/` (`v1.yaml` must match yours exactly, B6/B7; it now includes HLA-B);
   - `curation/`;
   - `tests/gold/`.
3. **Settings:** add B's fields from `src/drug_safety/config.py` to your settings. They are listed in each module README under "Environment variables" and in `.env.example`.
4. **Dependencies:** add the `rag`, `llm` and `ui` groups from `pyproject.toml`.
5. **Database:** copy `warehouse_proposal/` into your migrations as `0001`/`0002` (B1). Then load with `make store-labels store-papers store-cpic store-dpwg`.
6. **API:** `POST /v1/assess` calls `llm.explain_patient(...)`. It does retrieve → trim → explain and returns the `evidence`, `report`, `report_source` and `notice` for `AssessResponse` (D1).
   - Load the drug-name finder, the embedder and the chat model **once at start-up**.
   - Use `ui/stub_api.py` as the reference for endpoints and behaviour (D5), then delete it.
7. **Compose:** paste `docker/compose.ui.yml` into your `docker-compose.yml`. It assumes the API service is called `api` and listens on 8080 (D6).
8. **Pass the patient through unchanged**, including `lifestyle.diet.meals` (H3).
9. **Use only the agreed lifestyle values and diet tags** (H2).

---

## 7. Known faults and limitations

These are honest and current; fixed faults are listed in `docs/progress.md`.

| # | Fault or limitation | Why | What would fix it | Status |
|---|---|---|---|---|
| 1 | **The model can state wrong medicine.** In run 2: smoking "increases adverse reactions" with clozapine (wrong direction), "both drugs interfere with serotonin reuptake" (only sertraline does), "thrombosis" (not in the evidence), "amlodipine is a strong CYP3A4 inhibitor" (invented) | A 7B model paraphrasing, with nothing tying each claim to a passage; word rules can't judge meaning | **explain-v3** (built): the model must quote its supporting sentences word for word (rule 8), and may only say what they say. A person still reviews | Fix built and tested offline; **needs Colab run 3** |
| 2 | Most first answers are rejected for dose or stop wording, so they rely on the retry | The evidence itself says "avoid", "reduce the dose"; the model rewords it | v3 gives those sentences an allowed place (quotes) | Same as 1 |
| 3 | The validator's rule 6 is a list of patterns; new phrasings can slip through | Patterns can't cover all wording | Real-answer regression tests after every run; four gaps found and closed in run 2 | Ongoing |
| 4 | **Retrieval and LLM scores are self-graded** | No engine flags or gold set from Person A yet; the run-2 reading was done by an AI assistant | Real flags (F4), Person A's gold cases (C6), a person's review (H7) | Waiting for Person A |
| 5 | Thresholds tuned on B's own 28 flags: the paper distance cut-off (0.235) and the lifestyle terms | No other data yet | Recheck on real flags | Waiting for Person A |
| 6 | Some evidence is weak but on topic, e.g. a small celecoxib study in children | Papers are chosen by closeness, not study quality | Optional: a quality filter on papers | Accepted for now |
| 7 | Acenocoumarol has no label evidence | Its only label is French; the English translation is unreviewed | Someone who reads French reviews 13 passages (E7) | Waiting |
| 8 | 344 papers, below the spec's 500 | Licences: full text only where allowed | Accepted (F1) | Closed |
| 9 | The 37 curated rules are drafts | Need a pharmacist's or Person A's review | 10% sample check (C1) | Waiting |
| 10 | Each report takes about 46 s on a Colab L4 | Model size and one retry | A project GPU server; v3 should need fewer retries | Open |

---

## 8. Open questions for Person A

All open questions are in [docs/questions_for_person_a.md](docs/questions_for_person_a.md), each with B's proposal, so "agree" is a valid answer. **13 are open; the ⭐ ones block other work.**

- **New after run 2 (section H):**
  - **H1 ⭐ Chunk IDs changed:** treat them as valid for one load.
  - **H2 ⭐ Lifestyle values:** use only the agreed values and diet tags.
  - **H3:** pass `diet.meals` through unchanged.
  - **H4:** show the model's quotes on screen later? (contract 0.2.0)
  - **H5:** keep guided JSON as the default.
  - **H6 ⭐** Real flags and gold cases.
  - **H7 ⭐ Who confirms the manual review?**
  - **H8:** keep rule 6 strict.
- **Still open from before:**
  - A7: alcohol amount.
  - A10 ⭐: HLA-B and VKORC1 phenotype wording.
  - B7: HLA-B in your `scope/v1.yaml`.
  - C7: no flags for "no action" pairs.
  - E7 ⭐: who reviews the French translation.

Answered questions (2026-11-14, 43 of them) and what each changed are at the bottom of that file.

---

## 9. Sources and licences

| Source | Used for | Licence | Note |
|---|---|---|---|
| DailyMed (NLM) SPL labels | Tier-1 label evidence | Public domain (US government); DailyMed terms of use | 24 labels pinned in `scope/dailymed_labels.yaml` |
| openFDA, RxNorm, RxClass (NLM) | IDs, drug names, classes | Public / NLM terms | |
| CPIC | Gene–drug recommendations | CC0 1.0; attribution requested ("CPIC®, www.clinpgx.org") | E6 |
| DPWG via ClinPGx | Pairs CPIC lacks (warfarin, acenocoumarol, clozapine) | CC BY-SA 4.0 **plus "no sale for private or commercial use"** | E5: fine for this non-commercial project |
| PMC open-access papers | Tier-3 supporting evidence | CC BY, CC BY-NC (116 papers, marked); CC BY-NC-ND kept as metadata only | E1, F2: the project stays non-commercial |
| BDPM (ANSM), SINTROM 4 mg | Acenocoumarol (French) | Licence Ouverte 2.0: credit the source and date; translation allowed | Translations in `curation/translations/` |
| DDInter | Interaction rules (Person A's engine) | CC BY-NC-SA | Non-commercial (E1) |

Full review: [docs/sources.md](docs/sources.md).

**Text from these sources inside this repo:** test fixtures (`tests/llm/fixtures/`, `tests/gold/`), the curated quotes (`curation/`) and the review sheets in `docs/` contain short passages from the sources above, under their licences and credited on every chunk. All patients in this repo are **synthetic**. Nothing here is real patient data.

The project's own code has no licence file yet; choosing one is the project owners' decision.

---

## 10. Repository layout

| Path | Contents | For Person A |
|---|---|---|
| `src/drug_safety/contracts/` | Shared models and JSON schemas (0.1.0) | Shared |
| `src/drug_safety/ingest/` | Source loaders | Copy B's files |
| `src/drug_safety/rag/` | Chunkers, embeddings, store, retriever, retrieval evaluations | Copy |
| `src/drug_safety/llm/` | Prompt, client, validator, fallback, explain, LLM evaluation | Copy |
| `src/drug_safety/ui/` | Streamlit app, API client, stand-in API | Copy (delete `stub_api.py` once your API runs) |
| `src/drug_safety/curation/` | Checks and review sheet for the curated rules | Copy |
| `src/drug_safety/evaluation/` | The evaluation report | Copy |
| `src/drug_safety/config.py`, `scope.py`, `db.py` | Settings, scope loader, migrations runner | Merge into yours |
| `scope/` | v1 drugs and genes, pinned labels, aliases, classes, RxCUIs, LOINC codes | Shared (`v1.yaml` must match yours) |
| `curation/` | 37 draft rules with label quotes, diet tags, units, translations | Review (C1), then seed |
| `tests/` | Offline tests per module; `tests/gold/` gold flags and cases; `tests/llm/fixtures/` real Qwen answers | Copy |
| `notebooks/colab_qwen_vllm.ipynb` | Serves Qwen on Colab and runs the LLM evaluation | Until a GPU server exists |
| `docker/` | `ui.Dockerfile` and a proposed `ui` entry for your compose file | Copy |
| `warehouse_proposal/` | SQL for `kb.documents` and `kb.evidence_chunks` | Into your migrations (B1) |
| `docs/` | Questions for you, progress log, manual LLM review, sources, conventions, review sheets | Read |
| `Makefile`, `docker-compose.dev.yml` | Local development | Optional |
| `data/` | Not in the repo; rebuilt by the commands in section 4 | — |

---

## 11. Decisions so far

- **Embeddings:** `BAAI/bge-base-en-v1.5`, without the query instruction. A biomedical model wasn't needed (both models score 28/28).
- **Retrieval:** the best 2 tier-1 chunks plus 1 paper within cosine distance 0.235, instead of the spec's "closest 10, re-sort". The spec's rule gave 20/26 once papers were loaded (F3).
- **Chunking (2026-11-14):** one topic per chunk. Labels are cut at subsection and inner-heading boundaries; paper tables are one unit. Packing and size-only cuts mixed topics and hid the right passage.
- **LLM:** Qwen 2.5 7B Instruct on vLLM, reached through `MODEL_URL`. Guided JSON is the default. Prompt `explain-v3`.
- **Report contract unchanged:** the model's quotes are checked, then dropped. Showing them on screen is proposed for later (H4).
- **Safety:** a failed answer gets one retry, then the template report built from the flags alone. The screen always shows the disclaimer.
- **Data:** synthetic patients only on Colab. The project stays non-commercial (E1).
- **HLA-B:** added to v1 (E3), so there are 6 genes.

---

## 12. Where to read more

| Document | What's in it |
|---|---|
| [docs/progress.md](docs/progress.md) | Every step: what was built, how it was checked, what went wrong and how it was fixed |
| [docs/questions_for_person_a.md](docs/questions_for_person_a.md) | All questions, B's proposals, answers |
| [docs/llm_manual_review.md](docs/llm_manual_review.md) | Sentence-by-sentence reading of every Colab report |
| [docs/sources.md](docs/sources.md) | Source and licence review |
| [docs/conventions.md](docs/conventions.md) | ID and file formats |
| [docs/curation_review.md](docs/curation_review.md) | The 37 draft rules with their label quotes, for review |
| [docs/translation_review_sintrom.md](docs/translation_review_sintrom.md) | French/English side by side, for the reviewer |
| `src/drug_safety/*/README.md` | Per module: inputs, outputs, commands, checks, environment variables, troubleshooting |
