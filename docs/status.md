# Project status: done, left, and every fault

**Last updated:** 2026-11-14. This is Person B's half of Drug Safety RAG: evidence, retrieval, the LLM layer, the screen and the evaluation. Day-by-day detail is in [progress.md](progress.md). Questions for Person A are in [questions_for_person_a.md](questions_for_person_a.md).

**Where things stand:**
- Every Person B module is built and checked. `make check` passes 369 tests.
- **Biggest open fault:** the model can still state wrong medicine (F-1). The root-cause fix is built, but it hasn't been measured on the model; that needs Colab run 3.
- **Biggest dependency:** Person A's rule engine and API, needed for real flags and the end-to-end test.

---

## 1. Done

| Area | What exists | Proof |
|---|---|---|
| Contracts | `Patient`, `Flag`, `Report`, `AssessResponse`, version 0.1.0, agreed with Person A | 20 tests |
| FDA labels | 24 of 25 drugs (acenocoumarol has no US label), pinned and reviewed; openFDA IDs; drug classes (RxClass) | `make verify-dailymed verify-openfda verify-classes` |
| Label chunks | 1,519 chunks, one topic each (labels-v3), embedded with bge-base into PostgreSQL | `make verify-chunks verify-store` |
| Papers | 445 selected, 344 chunked (licence allows full text), 6,933 chunks; tables kept whole (papers-v2) | `make verify-papers verify-paper-chunks` |
| Guidelines | CPIC: 184 recommendations in 9 guidelines (CC0). DPWG: 4, for pairs CPIC lacks | `make verify-cpic verify-dpwg` |
| HLA-B | Added to v1 (6 genes); CPIC's allopurinol and phenytoin recommendations loaded; 2 gold flags | `make eval-flags` |
| French acenocoumarol label | Fetched; 13 passages translated, with a side-by-side review sheet | `make verify-bdpm` |
| Retriever | Flag → 2 tier-1 passages + 1 close paper. Rules: guideline phenotype, own label first, lifestyle factor named, no papers for lab flags | 28/28 gold flags |
| Curated rules | 37 lab and lifestyle rules drafted from label text, each with verbatim quotes | `make verify-curation` (682/682) |
| LLM layer | Prompt explain-v3, Qwen via vLLM (guided JSON), validator with 8 rules, one retry, template fallback | `make verify-llm`; 100 tests |
| Colab runs | Run 1 (explain-v1) and run 2 (explain-v2, plain and guided), every report read | [llm_manual_review.md](llm_manual_review.md) |
| Screen | Streamlit form or JSON upload → flags, evidence, report; sticky disclaimer; container image | `make verify-ui` (10/10) |
| Evaluation | Retrieval hit rate, faithfulness, fallback rate, latency, definition of done | `make eval-report` |
| Handover | README as the handover document; public repository; questions file with B's proposals | [README](../README.md) |

---

## 2. What is left

Owners: **B** = Person B · **A** = Person A · **Person** = any team member with the right skill.

| # | Task | Owner | Blocks | Next step |
|---|---|---|---|---|
| L-1 | **Colab run 3 with explain-v3**, then read every report | B | Fault F-1, definition of done item 5 | Run the notebook with `llm_bundle.zip` on an L4. Then `make eval-report`, and a "Run 3" section in llm_manual_review.md |
| L-2 | **Confirm the manual review** of runs 2 and 3 (spec: a human check) | Person, ideally clinically trained (H7) | Definition of done item 5 | Read `review.md`, then tick "Confirmed by a person" |
| L-3 | **Rule engine producing real flags** | A | L-4, L-5, F-4, F-5 | Send flags for the 10 test patients as soon as they exist (F4, H6) |
| L-4 | **Re-check retrieval on real flags**: the paper cut-off 0.235, the lifestyle terms, the gold set | B, after L-3 | F-4, F-5 | Rerun `make eval-flags` on real flags; adjust; document |
| L-5 | **Person A's gold cases**, reviewed by B (spec cross-check) | A, then B (C6) | F-4 | — |
| L-6 | **API `POST /v1/assess`** calling `llm.explain_patient` | A (D1) | Definition of done item 6 | Use `ui/stub_api.py` as the reference; then delete it |
| L-7 | **Integration** into the main repo: copy the modules, settings, dependencies, migrations | A, with B | L-6, L-8 | README section 6 |
| L-8 | **`docker compose up`** starts the whole stack | A (compose file); B's `ui` image is ready | Definition of done item 7 | Paste `docker/compose.ui.yml` (D6) |
| L-9 | **Review the 37 curated rules** (10% sample; 12 rows carry ⚠ questions) | A or a pharmacist (C1) | Seeding the engine's rule tables | `docs/curation_review.md` |
| L-10 | **French translation review**: 13 passages | Someone who reads French (E7) | Acenocoumarol label evidence (F-7) | `docs/translation_review_sintrom.md`; set `reviewed: true`, then `make chunk-bdpm store-bdpm` |
| L-11 | **Answer the open questions**: 13 open, 6 ⭐ | A | Several of the above | [questions_for_person_a.md](questions_for_person_a.md), sections A, B, C, E, H |
| L-12 | **Show the model's quotes on screen** (contract 0.2.0) | A + B, after L-1 (H4) | — | Only if run 3 shows the model copies reliably |
| L-13 | **Choose a licence** for the project's own code | Project owners | Reuse by others | The repo is public without one, so others may read it but not reuse it |
| L-14 | **A project GPU server** to replace Colab (needed for real patients) | Project (G3, G4) | Real-patient use, latency F-10 | Any machine with `vllm serve`; only 3 settings change |

---

## 3. Open faults

| # | Fault | Root cause | Fix and state | What is still needed |
|---|---|---|---|---|
| F-1 | **The model states wrong medicine.** Run 2: clozapine + smoking "increases adverse reactions" (wrong direction); "both drugs interfere with serotonin reuptake" (only sertraline does); "thrombosis" (not in the evidence); "amlodipine is a strong CYP3A4 inhibitor" (invented). No invented drug, dose or number | The model was asked to paraphrase, and nothing tied a claim to a passage, so it filled gaps from its own knowledge. Word rules can't judge meaning | **explain-v3, built:** each explanation must carry 1–3 sentences copied word for word from its evidence. Rule 8 checks them mechanically, and the prompt allows only what those quotes say. Tested offline: wrong, other-flag and too-short quotes are rejected, and the tests fail if the check is switched off | Colab run 3 (L-1), plus a person's review (L-2). A quote can be right while the paraphrase misreads it, so the human check stays |
| F-2 | **Most first answers are rejected for dose or stop wording** (21 of 22 first-try issues in run 2), so answers depend on the retry, and 2 in 10 still fall back to the template | The evidence itself says "avoid" or "reduce the dose", and the model rewords it. The prompt forbade that without giving it an allowed place | **explain-v3:** the evidence's instructions go in the quotes, not in the model's own words | Measure in run 3 (L-1) |
| F-3 | **Rule 6 is a list of patterns**; new phrasings can slip through (4 gaps found in run 2) | Patterns can't cover every wording | Every run's real answers become regression tests (`tests/llm/test_real_answers*.py`), and each gap is closed with a test | Repeat after every run. v3 should shrink the problem (F-2) |
| F-4 | **Scores are self-graded:** retrieval 28/28 on flags B wrote; the run-2 review done by an AI assistant | No engine flags or gold set yet; no human reviewer yet | Results are labelled "self-graded" in the README and report | L-3, L-4, L-5, L-2 |
| F-5 | **Thresholds tuned on B's own flags:** the paper distance cut-off (0.235) and the lifestyle trigger terms | No other data to tune on | Documented next to each setting | L-4 |
| F-6 | **Some evidence is weak but on topic**, e.g. a small paediatric celecoxib study for a CYP2C9 flag | Papers are chosen by closeness, not by study quality | None; it is honest evidence, marked as tier 3 | Optional: a paper-quality filter |
| F-7 | **Acenocoumarol has no label evidence** | Its only label is French; the translation isn't reviewed, and unreviewed text never becomes evidence | Its DPWG recommendation is used meanwhile | L-10 |
| F-8 | **344 papers instead of the spec's 500** | Licences: full text only where allowed; no-derivatives papers stay metadata only | Accepted by Person A (F1) | None (accepted deviation) |
| F-9 | **The 37 curated rules are unreviewed drafts** | Need a pharmacist's or Person A's check | Each row carries its label quotes; verify checks numbers and severity against them | L-9 |
| F-10 | **About 46 s per report** on a Colab L4 | Model size, plus one retry on most patients | Guided JSON and E-labels halved the call time (55 s to 27 s per call) | A GPU server (L-14); fewer retries with v3 |
| F-11 | **Rule 6 rejects restated flag reasons**, e.g. "she requires a lower dose of warfarin" (the flag's own reason) | A deliberate strictness choice | Kept strict: a rejected answer is retried, then falls back to the always-safe template | Person A to confirm (H8) |

---

## 4. Fixed faults (all, with root cause)

Grouped by area. "Checked" means a test or verify check now fails if the fault comes back.

### Labels and chunking
| Found | Fault | Root cause | Fix | Checked |
|---|---|---|---|---|
| Step 2 | Wrong labels: injections, a skin patch, a 2013 repackager copy | Automatic label choice | Labels pinned and reviewed in `scope/dailymed_labels.yaml`; `make check-updates` | verify-dailymed |
| Step 2 | Untitled sub-headings ("Bleeding", "Data") became tiny records | Parser split at every heading | Merged with their text | verify-dailymed |
| Step 2 | Old-format labels (ibuprofen, theophylline) seemed to lack interactions | They keep interactions under Precautions | Coverage check reads headings | verify-dailymed |
| Step 2 | Loading one drug wiped the manifest | Manifest rewritten whole | Only the loaded drugs are updated | Test |
| Step 2 | OTC warnings lost headings ("Ask a doctor before use if") | No heading element in OTC sections | The section code's name is used | verify-dailymed |
| Step 3 | 412 table rows split from their header | Tables split as text | Split by rows, repeating title and header | verify-chunks |
| Step 3 | Footnotes inside table header cells | Parser kept them inline | "[note N]" markers with notes under the table | verify-chunks |
| Step 3 | Gene tags missed ("cytochrome P450 2D6") | Spelling variants | Spellings added | Test |
| Step 3b | Wrong drug classes from openFDA (metformin as a sulfonylurea) | openFDA's class data | Switched to NLM RxClass | verify-classes |
| Step 3b | Brand labels marked "not from the holder" | Company-name matching | Each label's XML role is used | verify-openfda |
| Step 3b | "antiplatelet activity" tagged as a drug class | Bare word matched | Only class phrases ("antiplatelet agents") | Test |
| Step 3b | Good labels ruled out | A comma in the dosage form counted as a combination | Fixed parser | Test |
| 2026-11-14 | **Methotrexate renal flag got the hepatic section first** | **Subsections packed into one chunk** (8.4+8.5+8.6): the mixed vector (0.264) lost to hepatic (0.256); renal alone scores 0.208 | One subsection per chunk (labels-v3) | Tests; renal now first |
| 2026-11-14 | **Clozapine smoking paragraph shared a chunk with anticholinergic and QT text** | **Long subsections cut by size, across topics** | Cut at inner headings first; parent headings kept | Tests; the smoking chunk is clean |
| 2026-11-14 | 35 of 51 curated quotes pointed at old chunk IDs after re-chunking | Chunk numbering is per load | Re-pointed to the chunk holding the same text (text unchanged) | verify-curation 682/682 |

### Papers
| Found | Fault | Root cause | Fix | Checked |
|---|---|---|---|---|
| Step 6c | 5 error pages saved as papers | NCBI returns HTTP 200 on errors | Valid BioC JSON required | Test |
| Step 6c | "CC0" normalised to "CC" | Licence parsing | Fixed; public-domain papers kept | Test |
| Step 6c | A letter to the editor selected | No abstract check | Selection rule plus `reclassify` | verify-papers-selection |
| Step 6c | Chunks up to 512 tokens | Token counts don't add up when pieces are joined | Re-split until they fit | verify-paper-chunks |
| Step 6c | 521 chunks unreachable by the drug filter | Paper drugs not in the chunk text | Drugs and genes added to the context line | verify-paper-chunks |
| Step 6c | Reference lists stored as tables (184 chunks) | BioC marks them as tables | Dropped at parse time | Test |
| 2026-11-14 | **566 chunks were a table caption or footnote with no data** | Caption, body and footnotes chunked separately; a caption whose table didn't fit beside it went out alone | One unit per table; caption and footnotes as heading; no body → left out (23) | Test; 566 → 62 short chunks (real conclusions) |

### Retrieval
| Found | Fault | Root cause | Fix | Checked |
|---|---|---|---|---|
| Step 6c | Gold fell from 26/26 to 20/26 once papers were loaded | The spec's "closest 10" let papers crowd out labels | 2 tier-1 + 1 paper (F3) | eval-flags |
| Step 6c | Irrelevant papers for lab and lifestyle flags | No relevance bar | Distance cut-off 0.235; no papers for lab flags | eval-flags (recheck: F-5) |
| Step 6d | 3 of 8 gene flags got CPIC advice for the wrong phenotype (codeine ultrarapid got poor-metabolizer advice) | Embeddings barely tell phenotypes apart | A guideline chunk counts only for the same phenotype, at most one per flag | Tests; 11/11 |
| 2026-11-14 | Warfarin flag got a Celebrex passage | Another label mentioning warfarin was closer | Own label first (main slots only) | Test |
| 2026-11-14 | The first own-label version hurt methotrexate's fill-in slot | Applied to all slots | Main slots only | Test |
| 2026-11-14 | "HLA-B\*58:01 negative" counted as actionable | `.match` looks only at the start of the text | `.search`, in parser and retriever | Tests (fail if the bug returns) |
| 2026-11-14 | **Clozapine smoking flag: top passage about CYP2D6** | Embeddings weigh the drug and "levels" over the factor | Lifestyle flags: passages naming the factor first; dietary wording for vitamin K | Test; eval-flags 28/28 |

### LLM layer and validator
| Found | Fault | Root cause | Fix | Checked |
|---|---|---|---|---|
| Step 8 | Retry didn't fit an 8,192-token window | Prompt + failed answer + errors + new answer | vLLM started with 16,384 | verify-llm |
| Step 8 | The template report failed rule 6 | "poor metabolizer: lower dose requirement" split at the colon | Colons split only before a capital | Test |
| Step 8 | The full RxNorm list flagged ordinary words ("air", "lead") | Dictionary collisions | Prescribable subset plus a reviewed ignore list | verify-drug-names |
| Step 8 | A settings field shadowed a pydantic method | Name `model_json_schema` | Renamed `model_guided_json` | Test |
| Run 1 | Dose instructions in explanations passed ("Dose adjustments are necessary") | Rule 6 looked only for orders | Dose change stated as needed, in every field | Real-answer tests |
| Run 1 | "Consider alternative antiplatelet therapy" passed | Suggestions only had to start "Consider" | "Consider" plus a review verb | Real-answer tests |
| Run 1 | Harmless "Consider reviewing … dosage adjustments" rejected (2 needless fallbacks) | Too strict | Dose words count only as the action | Real-answer tests |
| Run 1 | A flag was left out on 4 of 10 first tries | The prompt didn't list the flags | Exact flag list at the end of the prompt | Run 2: 0 left out |
| Run 1 | Answers slow; a third of each answer was copied IDs | 60-character chunk IDs | Evidence labelled `E1`, `E2` | Run 2: 27 s per call (was 55) |
| Run 2 | Broken JSON and 3/10 fallbacks in plain mode | Free-form output | **Guided JSON default** | Run 2 guided: 0 broken, 0 fallbacks |
| Run 2 | 4 dose or switch phrasings passed: "necessitates a lower starting dose", "adjusting it", "adjusting clopidogrel", "considering alternative … therapies" | Pattern gaps | Patterns added | Real-answer tests (40 attempts) |
| Run 2 | **Diet flags had no visible reason** (p02 kale and spinach, p07 grapefruit) | **The patient summary listed fields by hand, so `diet.meals` was silently dropped** | Every patient field shown; a test walks the contract | Test fails if a field is dropped |

### Screen, evaluation, infrastructure
| Found | Fault | Root cause | Fix | Checked |
|---|---|---|---|---|
| Step 9 | Disclaimer banner didn't stay on screen | Streamlit wraps elements | Sticky CSS on the wrapper | verify-ui |
| Step 9 | Result appeared below a long form | Layout | Form collapses into an expander | verify-ui |
| Step 9b | API address shown as `host\.docker\.internal` | Escaping inside a code span | Shown raw | Test |
| Step 9b | CPIC headings repeated the title, then the gene | Heading built from the title | Heading = phenotype · population | verify-cpic |
| Step 10 | Two module READMEs lacked environment variables | Missed | Added; the report checks every README | Test |
| Step 10 | First-call timing called "model load" | Wrong measurement | Model load timed itself | — |
| 2026-11-14 | Empty French source crashed the store | The embedder was called with no texts | Empty sources allowed | Test |
| 2026-11-14 | Wrong notes on the French label: "Licence Ouverte 1.0", "updated 2018" | Not re-read at the source | Corrected to 2.0 and 2025-06-20, checked on the page | verify-bdpm |
| 2026-11-14 | Report said "26 hand-written" gold flags (there are 28); latency taken from the plain run | Hard-coded number; wrong run picked | Computed; guided (default) run used | eval-report |
| 2026-11-14 | Commit message carried a tool attribution line | Default template | Removed before the first push | git log |

### Mistakes in Person B's own checks (caught and fixed)
These are listed because each one could have hidden a real fault.
- **Table check:** the first version raised 6 false alarms, because one sub-header appears in two tables.
- **Paper text-loss check:** rebuilt three times (short passages, chunk boundaries, overlaps) before it was right.
- **First re-judge script for run 2:** mapped the E-labels the wrong way round, so every citation looked invalid. Caught at once.
- **Two curation test bugs:** an export test recursed into itself, and another counted a word instead of rows.

---

## 5. How to keep this file current
- **After a Colab run:** update section 2 (L-1, L-2) and sections 3 and 4 (F-1 to F-3).
- **When Person A answers a question:** update the matching L or F row and the questions file.
- **When a fault is fixed:** move it from section 3 to section 4 with its root cause, and add the test that guards it.
