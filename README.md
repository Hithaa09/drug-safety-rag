# Drug Safety RAG: Person B modules

Person B's half of the Drug Safety RAG project (design: `docs/spec.pdf`, the project document):
FDA labels and research papers into the document store, the RAG retriever, the Qwen LLM layer
with its validator, and the Streamlit screen.

**Rules decide what is unsafe (Person A). RAG finds the evidence and Qwen only explains (Person B).**
Output is decision support for a pharmacist or clinician, not medical advice.

## Progress

| Step | What | Status | How to check it yourself |
|---|---|---|---|
| 1 | Workspace setup | Done | `make check` |
| 1b | Contracts draft (`Patient`, `Flag`, `Report`, API response) | **Draft done**; waiting for Person A's review | `pytest tests/contracts`; questions in the contracts README |
| 2 | DailyMed label loader | **Done**, 24/25 labels | `make verify-dailymed`, which should end in `RESULT: PASS` |
| 3 | Label chunking (998 chunks) | **Done** | `make verify-chunks`, which should end in `RESULT: PASS` |
| 3b | openFDA (who made each label, better label picks, IDs), drug classes (RxClass), label update check | **Done**; 3 label swaps approved and applied | `make verify-openfda`, `make verify-classes`, `make check-updates` |
| 4a | Embedding model choice (retrieval test, 26 gold cases) | **Done**: bge-base kept, 26/26 | `make eval-retrieval`, which should end in `RESULT: PASS` |
| 4b | Embeddings into PostgreSQL (`kb.evidence_chunks`) | **Done**: 998 chunks, 26/26 gold via SQL | `make verify-store`, which should end in `RESULT: PASS` |
| 5 | Rule curation from labels (`lab_drug_rules`, `lifestyle_drug`) | **37 rows drafted, waiting for review** | `make verify-curation`; review sheet `docs/curation_review.md` |
| 6a–b | Paper availability measured; papers selected | **Done**: 446 selected (~330 clearly relevant), below the spec's 500 | `make verify-papers-selection` |
| 6c | Paper full text, licence check, chunk, embed, load | **Done**: 344 papers, 7,551 chunks; gold 26/26 with papers loaded | `make verify-papers`, `verify-paper-chunks`, `verify-store` |
| 6d | CPIC / DPWG guideline text (tier 1) | **CPIC done**: 171 recommendations (CC0), 8 of 9 gene flags get theirs; **DPWG waiting** for Person A (licence, E5) | `make verify-cpic`, `make eval-flags` |
| 7 | Retriever (`Flag` → cited evidence) | **Done**: 26/26 gold flags through the contract | `make eval-flags`, which should end in `RESULT: PASS` |
| 8 | LLM layer and validator (`llm`) | **Built**; first Qwen run done (5/10 first try, 0 invented facts); fixes made (`explain-v2`); re-run on Colab pending | `make verify-llm` (`RESULT: PASS`, 62 tests); then the Colab notebook ([`notebooks/colab_qwen_vllm.ipynb`](notebooks/colab_qwen_vllm.ipynb)) |
| 9 | Streamlit UI (`ui`) | **Built**; checked against a stand-in API (no rule engine yet) | `make verify-ui` (`RESULT: PASS`, 59 tests); try it: `make stub-api` + `make ui` |
| 10 | Evaluation (retrieval hit rate, faithfulness, fallback, latency, definition of done) | **Built**; retrieval 26/26, latency 0.09 s without the LLM; LLM numbers for the current prompt wait for Colab | `make eval-report` → `data/outputs/<date>_evaluation/report.md` |

Details of what was built, checked and decided at each step: [docs/progress.md](docs/progress.md).

## First-time setup

Needs Python 3.11+ (and Docker Desktop for the database, from step 4).

```bash
make setup            # creates .venv, installs everything, creates data/ folders and .env
make check            # lint + all tests; should end with "passed"
make ingest-dailymed  # downloads the 25 FDA labels (about 1 minute)
make verify-dailymed  # should end with "RESULT: PASS"
make fetch-openfda    # who made each label; proposes better labels
make fetch-classes    # drug classes (needed before chunking)
make chunk-labels     # splits the labels into evidence chunks
make verify-chunks    # should end with "RESULT: PASS"
make db && make db-migrate   # local PostgreSQL + tables (Docker Desktop running)
make store-labels     # embed + load the chunks
make verify-store     # should end with "RESULT: PASS"
make help             # every command
```

## Layout

| Path | Contents | Hand over to Person A? |
|---|---|---|
| `src/drug_safety/ingest/` | `dailymed` (done), `openfda`, `pmc` loaders | Yes |
| `src/drug_safety/rag/` | Chunking, embeddings, retriever | Yes |
| `src/drug_safety/llm/` | Qwen client, prompts, validator, fallback | Yes |
| `src/drug_safety/ui/` | Streamlit app | Yes |
| `src/drug_safety/evaluation/` | Person B's evaluation report (spec "Evaluation of the full pipeline", definition of done) | Yes |
| `docs/llm_manual_review.md` | The hand check of LLM reports, one section per Colab run | Yes |
| `docker/` | The `ui` image (`ui.Dockerfile`) and a proposed `ui` entry for Person A's `docker-compose.yml` | Yes |
| `notebooks/colab_qwen_vllm.ipynb` | Serves Qwen with vLLM on Google Colab and runs the LLM evaluation | Yes (until a GPU server exists) |
| `scope/drug_name_ignore.yaml` | Words the validator's drug-name check must not treat as drugs | Yes |
| `src/drug_safety/contracts/` | Draft shared models + JSON schemas | Give to A for review |
| `tests/` | Tests for the folders above (all offline) | Yes |
| `scope/v1.yaml` | The 25 drugs and 6 genes (HLA-B added 2026-11-14) (must match Person A's) | Shared |
| `scope/dailymed_labels.yaml` | Which FDA label is used for each drug | Yes |
| `scope/drug_aliases.yaml` | Temporary drug and gene name dictionary for tagging | Replace with A's `drug_alias` |
| `scope/drug_classes.yaml` | Drug classes named in label text, and which RxClass class each means | Yes |
| `docs/conventions.md` | ID and file formats Person B writes | Shared, agree with A |
| `curation/` | Curated rules from label text, checks' reference tables, coverage notes | Yes (approved rows as CSV) |
| `scope/rxcui.yaml`, `scope/lab_codes.yaml` | Verified RxCUIs and LOINC codes | Merge with A's `drug_dim` |
| `docs/progress.md` | What each step built, how it was checked, open decisions | Yes |
| `src/drug_safety/config.py`, `scope.py` | Settings and scope loader | Merge into A's |
| `warehouse_proposal/` | SQL for the document-store tables | Proposal for A's warehouse |
| `docker-compose.dev.yml`, `Makefile` | Local development only | No |
| `data/` | Downloaded and generated files (git-ignored) | No; rebuilt by the commands |

Every module folder has its own README with inputs, outputs, how to run it, how to verify it,
and troubleshooting.

## Handing over to Person A

The package path is `src/drug_safety/<module>/`, the same as in the main repo, so handover is a
copy with no renaming:

1. Copy `src/drug_safety/{ingest,rag,llm,ui,evaluation}` and `tests/{ingest,rag,llm,ui,evaluation}` into the main repo.
   For `ingest/`, copy only Person B's files (`dailymed.py`, `openfda.py`, `rxclass.py`,
   `pubmed_select.py`, `pmc.py`, `rxnorm_names.py`, `cpic.py`).
2. Copy `scope/dailymed_labels.yaml`.
3. Add any missing fields from `config.py` to the main repo's settings (names follow spec p20).
4. Add the `rag`, `llm` and `ui` dependency groups from `pyproject.toml`.

## What Person B needs from Person A

| Need | Used for | Until it arrives |
|---|---|---|
| `contracts` (`Flag`, `Report`, `Patient`) | Input and output of `rag` and `llm` | **Draft ready for review**: 9 questions, A1–A9 in [docs/questions_for_person_a.md](docs/questions_for_person_a.md) |
| `drug_alias` table | Tagging chunks with `drugs[]`; validator check 4 | A small hand-made dictionary for the 25 drugs |
| `drug_dim` RxCUIs | Curated rule rows | Look up the 25 RxCUIs on RxNav |
| Real flags | Testing the retriever and the LLM | 26 hand-written engine-style flags; 10 synthetic patients for the LLM |
| The API | The Streamlit UI | `ui/stub_api.py`: same endpoints and contract; saved flags and evidence for the 10 example patients, with the real LLM layer |

## Open decisions

**Person A answered all questions on 2026-11-14**; most rows in the table below are now decided.
The up-to-date status (and the few still open: A7, A10, B7, C7, E7) is in the questions file.

**All questions for Person A, with B's proposals and space for answers:**
[docs/questions_for_person_a.md](docs/questions_for_person_a.md). The ⭐ ones block other work.

Source review (what was checked, and the verdict for each): [docs/sources.md](docs/sources.md).


| Decision | Who | Detail |
|---|---|---|
| Acenocoumarol label source | B + A | Kept in v1 (Person A confirmed it's approved outside the US). Proposed: French BDPM Sintrom label (Licence Ouverte 1.0: credit the source and date, don't alter) with a reviewed English translation. Gene–drug from DPWG via ClinPGx. Waiting for Person A |
| 6 labels marked `check` | B | Usable; not repackager problems but formulation choices (OTC, extended-release) or no holder label. See `scope/dailymed_labels.yaml` |
| Is the project commercial, now or later? | B + A | If yes, DDInter (CC BY-NC-SA) must be replaced. See docs/sources.md |
| Add HLA-B to the v1 genes? | B + A | **Decided 2026-11-14: yes** (E3). Added; CPIC's HLA-B recommendations loaded; 2 gold flags added |
| `drugs[]` as names or RxCUIs? | B + A | Chunks store ingredient names now; switch to RxCUIs if the `Flag` contract uses them (reload, no re-embedding) |
| Approve the 37 curated rules | B (or a pharmacist) | `docs/curation_review.md`; 12 rows carry ⚠ questions |
| Papers below the spec's 500 | B + A | 445 selected; **344 chunked** (full-text licence); the spec's DoD says at least 500. Accept, or widen scope |
| 80 papers under no-derivatives licences (CC BY-NC-ND) | B (+ legal advice if needed) | Kept metadata-only because chunking is a derivative; including them would give 424 chunked papers |
| Non-commercial papers (CC BY-NC, 116) | B + A | Fine for an academic project; must be removed if it becomes commercial (marked per paper) |
| Retrieval rule differs from the spec's wording | B + A | Tier-balanced selection (best 2 labels + 1 close paper); measured 26/26 vs the spec's 20/26 once papers are loaded |
| Validator rule 7 (no invented numbers), added by B | B + A | Not in the spec's 6 rules, but in its prompt rules. Its effect on the fallback rate is reported separately; drop it if it costs too much |
| Validator rule 4 strictness | B + A | As the spec says, a drug named only in the evidence (e.g. prasugrel in CPIC text) is rejected; Qwen must say "an alternative antiplatelet" |
| `/health` body shape | A (B suggests) | The UI shows any body; `{"status", "checks": {name: {ok, detail}}}` displays best. See `ui/README.md` |
| Real patients through Colab? | B + A | No: Colab is Google's machine. Synthetic data only until a project GPU server exists |

## Decisions made

- 2026-11-11: Person B works in a separate workspace with the main repo's package paths, so work
  can be copied in without renaming.
- 2026-11-11: The LLM is served by vLLM, reached via `MODEL_URL`; for now on Google Colab (the notebook), later on a GPU server, with no code change.
- 2026-11-11: Embedding model `BAAI/bge-base-en-v1.5` kept, without the query instruction: 26/26 gold label cases in the top 3 (MRR 0.98); no biomedical model needed.
- 2026-11-11: Retrieval selects the best 2 tier-1 chunks plus 1 paper within cosine distance 0.235 (measured: spec wording gave 20/26 gold once papers were loaded; this gives 26/26).
- 2026-11-11: DailyMed labels are pinned per drug in `scope/dailymed_labels.yaml`, because
  automatic choice proved unreliable.
