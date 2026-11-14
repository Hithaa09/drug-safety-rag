# Progress log (Person B)

One entry per step: what was built, how it was checked, what was fixed, and what's still open.
Newest at the bottom.

---

## Step 1: Workspace setup (2026-11-11)

**Built:** Person B workspace with the main repo's package layout (`src/drug_safety/`), settings
read from environment variables, the v1 scope file, a local database compose file, and a Makefile.

**Checked:** installs cleanly; lint passes; tests confirm the modules import, settings come from
the environment, and the scope has 25 drugs, 5 genes and 3 lab panels.

**Not checked:** the local database (Docker Desktop wasn't running). It's first needed in step 4.

---

## Step 2: DailyMed label loader (2026-11-11)

**Built:** [`src/drug_safety/ingest/dailymed.py`](../src/drug_safety/ingest/dailymed.py), with
the commands `load`, `suggest` and `verify`, and the pinned label list
[`scope/dailymed_labels.yaml`](../scope/dailymed_labels.yaml). Full guide:
[ingest README](../src/drug_safety/ingest/README.md).

**Result:** 24 of 25 labels loaded (22 prescription, 2 OTC). Acenocoumarol has no US label.
5.4 MB of raw XML and 1.9 MB of parsed JSON.

**How it was checked:**

| Check | Result |
|---|---|
| 27 offline unit tests (parsing, rendering, loading, manifest, verify) | Pass |
| Live run on clopidogrel: 12.5 Pharmacogenomics, 7.2 CYP2C19 Inhibitors (omeprazole warning) and 8.6 Renal Impairment read correctly against the label | Pass |
| Live run on all 25 drugs | 24 ok, 1 missing, 0 errors |
| `make verify-dailymed` on the real output | 290/290 checks pass |

**Problems found while checking, and fixed:**

1. Automatic label choice picked injections, a skin patch and a 2013 repackager copy, so labels
   are now pinned and reviewed.
2. Untitled sub-headings ("Bleeding", "Data") became tiny separate records; they're now merged
   with their text.
3. Old-format labels (ibuprofen, theophylline) keep interactions under Precautions, so the
   coverage check now looks at headings.
4. Loading one drug wiped the manifest; it now updates only the drugs that were loaded.
5. OTC warnings lost headings such as "Ask a doctor before use if"; the section code's name is
   now used as the heading.

**Open:**
- Acenocoumarol: kept in v1; label source proposed (see update below), waiting for Person A.
- 9 labels marked `check` for a better choice later: omeprazole, aspirin, ibuprofen, diclofenac,
  metformin, tramadol, amitriptyline, theophylline, paracetamol.
- Loading into `kb.documents` waits for the table schema to be agreed with Person A.

**Update 2026-11-11, acenocoumarol:** Person A confirmed it's approved and usable (outside the
US), so it stays in v1. Checked sources: the French BDPM lists Sintrom 4 mg (CIS 61510352), with
full product information covering contraindications (including severe renal and moderate hepatic
impairment), interactions (including alcohol), vitamin K and CYP2C9. Licence Ouverte 1.0: credit
the source and update date and don't alter the data. Text is in French, last updated June 2018.
The UK eMC's reuse terms are unverified. Gene–drug guidance is DPWG (CYP2C9, VKORC1, CYP4F2;
level 1B) via ClinPGx. Proposal sent to Person A; a BDPM loader follows after chunking once agreed.

---

## Step 3: Label chunking (2026-11-11)

**Built:** [`src/drug_safety/rag/chunking.py`](../src/drug_safety/rag/chunking.py) (`run`,
`verify`), [`src/drug_safety/rag/tagging.py`](../src/drug_safety/rag/tagging.py), and the
temporary dictionary [`scope/drug_aliases.yaml`](../scope/drug_aliases.yaml). Full guide:
[rag README](../src/drug_safety/rag/README.md).

**Result:** 988 chunks from 24 labels; tokens min 31, median 333, max 478 (limit 480; the
embedding model's ceiling is 512). 121 chunks carry gene tags.

**How it was checked:**

| Check | Result |
|---|---|
| 54 offline unit tests across the project (27 new: chunking, tagging, verify) | Pass |
| `make verify-chunks` on real data (25 checks, including 11 gold spot checks from the spec's examples) | 25/25 pass |
| `make verify-dailymed` after the parser change | Pass |
| Read real chunks by hand (contraindications, the clopidogrel 7.2 interaction, the 12.5 pharmacogenomics table, OTC aspirin warnings) | Correct |

**Problems found while checking, and fixed:**

1. **Long tables were split without their header row:** the clopidogrel CYP2C19 table's second
   piece had numbers but no column names. A new verify check found **412 such rows** across the
   labels. Tables are now split by rows, repeating the title and header in every piece; 0 left.
2. **Footnotes were mixed into table header cells.** The DailyMed parser now turns them into
   "[note N]" markers with "Note N: ..." lines under the table. The labels were re-parsed
   (verify flagged all 13 affected labels as out of date until they were).
3. **Gene tags were missed in older labels** that write "cytochrome P450 2D6"; spellings added
   (amitriptyline now tagged CYP2D6).
4. My first version of the table check raised 6 false alarms, because the same sub-header line
   appears in two tables. Fixed in the check, not the data.

**Open:**
- `scope/drug_aliases.yaml` is temporary; replace with Person A's `drug_alias` table.
- Clinical-trial narratives are dropped; revisit if retrieval evaluation (step 10) shows a need.

---

## Step 3b: openFDA, drug classes, label update check (2026-11-11)

**Built:**
- [`ingest/openfda.py`](../src/drug_safety/ingest/openfda.py): application holder, who made
  each pinned label (manufacturer or holder / repackager / unclear), proposed better labels, and
  an ID file for Person A (`data/processed/openfda/ids_for_person_a.csv`).
- [`ingest/rxclass.py`](../src/drug_safety/ingest/rxclass.py) and
  [`scope/drug_classes.yaml`](../scope/drug_classes.yaml): 9 drug classes from NLM RxClass.
- Chunks now carry `drug_classes` and `class_drugs`, kept separate from `drugs`.
- `make check-updates`: compares each loaded label with DailyMed's newest version.

**Result:** 4 pinned labels are repackager copies; 3 have a better label from the application
holder (proposed below); theophylline's holder has no label of its own. 9 labels are "unclear"
and are all the same company under another name. 316 of 988 chunks name a drug class.
All 24 labels are up to date.

**How it was checked:**

| Check | Result |
|---|---|
| 80 offline unit tests | Pass |
| `make verify-openfda` | Pass |
| `make verify-classes`: every class has exactly the expected members | 13/13 pass |
| `make verify-chunks`, now with class tags and 3 class spot checks | 28/28 pass |
| `make check-updates` | 24/24 up to date |
| The 3 proposed labels checked on DailyMed: current, prescription, same oral form, has interactions and contraindications | Pass |
| Class tags read by hand ("coumarin-type anticoagulants" in the ibuprofen label, "Calcium Channel Blockers" in the Zocor table) | Correct |

**Problems found while checking, and fixed:**

1. **openFDA's drug classes were wrong** (metformin listed as a sulfonylurea; combination-product
   classes on amlodipine and paracetamol) and empty for six drugs. Rejected; switched to NLM RxClass.
2. **Name matching alone marked brand labels as "not from the holder"** (Viatris vs Upjohn, Teva
   vs Barr). Each label's own XML now records whether the company manufactures or repacks, and
   unclear cases are shown rather than guessed.
3. **The bare word "antiplatelet"** would have tagged "antiplatelet activity of Plavix" as a drug
   class; only phrases like "antiplatelet agents" count now.
4. **A bug in the label-choice helper** (`is_single_ingredient_oral`): a comma in the dosage form
   ("CAPSULE, DELAYED RELEASE") counted as a combination product, which ruled out good labels.
   Fixed and tested; all current pins re-checked.

**Waiting for approval (Person B):**

| Drug | Current pin | Proposed |
|---|---|---|
| omeprazole | Advanced Rx (repackager) | Glenmark, the application holder (`c6c22be1-659d-4e71-b292-701e27da201b`) |
| ibuprofen | Proficient Rx (repackager) | Aurobindo, the application holder (`00872852-680a-41b3-901b-b991b12a176d`) |
| amitriptyline | RemedyRepack (repackager) | Rising Pharma, the application holder (`a219a68e-7ce5-4e9d-a0de-ec76960040f6`) |

**Update 2026-11-11, swaps approved and applied:** omeprazole → Glenmark, ibuprofen → Aurobindo,
amitriptyline → Rising Pharma (all now `review: ok`; all three now classified as
`holder_or_manufacturer`). Before reloading, `verify-dailymed` correctly failed on the three
changed pins. After reloading, re-fetching and re-chunking: 80 tests pass; verify-dailymed
290/290, verify-openfda 96/96, verify-classes 13/13, verify-chunks 28/28; all labels up to date.
998 chunks (median 328 tokens, max 477). 6 labels remain `check`: aspirin, paracetamol (OTC),
metformin, tramadol (extended-release), diclofenac (less common form), theophylline (holder has
no label of its own).

---

## Step 4a: Embedding model choice (2026-11-11)

**Built:** [`rag/embeddings.py`](../src/drug_safety/rag/embeddings.py) (LangChain Hugging Face
embeddings with an on-disk cache), [`rag/eval_retrieval.py`](../src/drug_safety/rag/eval_retrieval.py),
and 26 gold cases in [`tests/gold/retrieval_labels.yaml`](../tests/gold/retrieval_labels.yaml)
covering all four flag types, written before any model was run.

**Result:** `BAAI/bge-base-en-v1.5` found the expected label passage in the top 3 for **26 of 26**
cases (MRR 0.981 without the query instruction, 0.962 with it; random order would give 26%).
Without the drug filter: 25/26. **Decision: keep bge-base, no query instruction; no biomedical model needed.**

**How it was checked:**

| Check | Result |
|---|---|
| Every gold case has at least one chunk that can match it (checked before running the model) | 26/26, after one wording fix (allopurinol) |
| hit@3 with the spec's search (filter, cosine top 10, tier re-sort, keep 3) | 26/26 |
| Harder test without the drug filter | 25/26 |
| First-ranked passage read by hand for every case | Clinically right in all 26 |
| 85 offline unit tests | Pass |

**Fixed before running the model:** the allopurinol expectation looked for "creatinine
clearance", but this label states renal dosing by eGFR under "Renal Impairment".

**Not done yet:** storing the embeddings in PostgreSQL (step 4b), which needs Docker Desktop.

---

## Step 4b: Embeddings into PostgreSQL (2026-11-11)

**Built:** [`src/drug_safety/db.py`](../src/drug_safety/db.py) (connection, migrations),
[`rag/store.py`](../src/drug_safety/rag/store.py) (`load`, `verify`, `remove`), and the table
proposal [`warehouse_proposal/0001_kb_document_store.sql`](../warehouse_proposal/0001_kb_document_store.sql).

**Result:** 24 documents and 998 chunks with 768-dimension bge-base vectors in
`kb.documents` / `kb.evidence_chunks`. The spec's pgvector search finds the expected passage
for **26/26** gold cases and returns exactly the same top 3 as the in-memory test. About 7 ms per
search without a vector index.

**How it was checked:**

| Check | Result |
|---|---|
| `make verify-store` (counts, tags, vector size, model, vectors match their text, gold via SQL, SQL = in-memory) | 9/9 pass |
| Migration run twice | Second run does nothing |
| Load run twice | Same counts, one load_id, no duplicates |
| Remove by load_id, then reload | 0 rows after removal; 9/9 after reload |
| 4 database tests on a throwaway database, including **a forced failure that must change nothing** | Pass |
| 89 tests in total | Pass |
| The real database after the tests | Untouched (9/9 still pass) |

**Problems found while checking, and fixed:**
1. pgvector returned its own `Vector` type, not an array, which crashed the vector check;
   converted explicitly.
2. **verify crashed on an empty table** instead of reporting FAIL; it now stops with
   "table is empty; run make store-labels".

**Open:** whether `drugs[]` holds ingredient names (as now) or RxCUIs depends on the `Flag`
contract agreed with Person A (a switch means reloading, not re-embedding).

---

## Step 1b: Contracts draft (2026-11-11)

**Built:** [`src/drug_safety/contracts/`](../src/drug_safety/contracts/): `Patient`, `Flag`,
`DataGap`, `Report`, `EvidenceChunk`, `AssessRequest`, `AssessResponse`, plus generated JSON
schemas. A draft for Person A, with 5 questions in its README.

**How it was checked:**

| Check | Result |
|---|---|
| The spec's Report examples (p16, p19), kept verbatim | Valid |
| The worked-example patient (p18), its three flags and a full response | Valid |
| The spec's Flag example (p14), verbatim | Rejected on purpose (gene and phenotype now required); valid with them. Question 1 for Person A |
| 19 contract tests (spec examples, rejections, consistency, schema drift, pydantic only) | Pass |
| **Deliberate breaks to the models** (allow unknown fields; no per-type trigger check; uncited suggestions allowed) | Each caught by its tests; original restored unchanged |

**Fixed while checking:** my first attempt at the deliberate-break test showed no results
because pytest's output was too quiet; rerun reading pytest's exit code and the failed test names.

**Open:** Person A's answers to the 5 questions; then the version moves from `0.1.0-draft` to `0.1.0`.

---

## Step 5: Rule curation from labels (2026-11-11)

**Built:** 37 draft rules in [`curation/`](../curation/) (10 lab-drug, 27 lifestyle-drug), each
with exact label quotes; reference tables (severity phrases, units, diet tags), coverage notes,
verified IDs ([`scope/rxcui.yaml`](../scope/rxcui.yaml), [`scope/lab_codes.yaml`](../scope/lab_codes.yaml)),
the checker [`src/drug_safety/curation/rules.py`](../src/drug_safety/curation/rules.py) and the
review sheet [`docs/curation_review.md`](curation_review.md). **None approved yet.**

**How it was checked:**

| Check | Result |
|---|---|
| RxCUIs from RxNav: one ingredient-level code per drug, cross-checked with CPIC (22 drugs CPIC lists all agree) | Pass |
| 12 LOINC codes looked up on NLM Clinical Tables | All exist with the expected names |
| `make verify-curation` (quotes, label source, numbers, units, operator wording, severity phrase, proxies, contract values, coverage, required rules) | 682/682 pass |
| 16 tests, 13 of them each plant one mistake that verify must catch | Pass |
| Every rule read clinically, then every coverage-note claim re-checked against the label | 12 rows flagged for the reviewer; 3 notes made more precise |
| 2 new contract tests' worth of questions | Added to the contracts README (questions 6–9) |

**Problems found while checking, and fixed:**

1. **verify caught 3 drafting errors:** allopurinol wrongly treated as a CLcr proxy (a design bug: the
   proxy now depends on the label's measure, not its unit); warfarin's phrase missed because the
   source has "decreasedINR" without a space; **aspirin's quote didn't support its severity**.
   With the full stomach-bleeding warning it's major, not moderate.
2. My first edit script silently stopped (code reflowed by the formatter); spotted because the
   same 3 failures came back; fixes then applied one by one.
3. **A meaningless test** (`... or True`) hid a real gap: the operator check only handled "below"
   rules. It now checks both directions, and the test asserts it.
4. An export test recursed into itself, and another counted a word instead of rows; both were
   test bugs, fixed. The export itself was right.
5. **Inconsistency in my drafts:** diclofenac and celecoxib have the same GI-bleeding sentence as
   ibuprofen but had no rows; 4 rows added. Phenytoin's chronic-alcohol row added.
6. Rule IDs said `any` for rules that only cover regular and heavy drinkers; IDs now list the values.

**Open:** approval of the 37 rows (12 with ⚠ questions); contract questions 6–9 for Person A;
the engine should report the most severe of several rules on one drug and measure (note for Person A).

---

## Step 6a: How many usable papers exist? (2026-11-11; measurement only, nothing fetched)

Counts from NCBI E-utilities (PubMed), open access = `"pubmed pmc open access"[filter]` (full text
reusable; "free full text" is NOT the same and was rejected). Raw numbers and PMIDs:
`data/outputs/2026-11-11_paper_availability/`.

| Search | Open-access papers | Relevant in a random sample (read by hand) |
|---|---|---|
| Spec query, tiers 1–3 by publication type | 338 (3 guidelines, 51 reviews or meta-analyses, 284 trials) | — |
| + observational designs by MeSH | 521 | 15 of 40 clearly, 11 marginal, **14 off topic** (v1 drug only a probe or comparator) |
| Drug–gene, tiers 1–3 | 355 | ~10 of 15 |
| v1 drug–drug, word search | 318 | ~5 of 15 |
| Drug–lifestyle, word search | 1,842 | ~0–1 of 15 (matches anything mentioning diet or risk) |
| Drug–lifestyle, MeSH | 502 | ~2 of 12 (mouse and cancer studies) |
| v1 drug–drug, MeSH "Drug Interactions" | 68 | ~6 of 12 |
| **Drug–gene, any study type, humans, gene in title/abstract** | **701** | **~13 of 20 relevant, 3 marginal, 4 off topic** |

**Findings:**
- Only 3 guidelines are open access; CPIC guidelines are mostly NIH author manuscripts in PMC, which
  aren't open-access licensed, so guideline text needs its own source and licence check.
- Strict tiers 1–3 give about 350 relevant papers, below the spec's 500.
- Drug–gene papers (701) plus MeSH drug–drug papers (68) give 764 candidates, about 65% relevant,
  so roughly 500 after a relevance filter.
- Lifestyle literature searches are too imprecise to use; lifestyle evidence comes from the labels
  and the FDA CYP table instead.

---

## Step 6b: Paper selection (2026-11-11)

**Decision (Person B):** quality filter, aim for 500, report the honest count.

**Built:** [`ingest/pubmed_select.py`](../src/drug_safety/ingest/pubmed_select.py): searches,
PubMed metadata, 13 ordered rules, tiers, an exclusion reason for every paper. Guide: ingest README.

**Result:** 764 candidates → **446 selected** (tier 1: 1, tier 2: 28, tier 3: 417). By hand-read
fresh samples: ~74% clearly relevant, ~16% marginal, ~10% off topic, so **about 330 clearly
relevant, below the spec's 500.**

**How it was checked:**

| Check | Result |
|---|---|
| `make verify-papers-selection` | Pass |
| 19 rule tests | Pass |
| 4 hand-read samples (50, 30 of the exclusions, fresh 50, final fresh 50) | Precision went from 54% to 74% relevant; every rule change was judged on a new seed |

**Found and fixed while checking:**
1. 18% off topic at first: lab and computer studies on human cells passed the "Humans" filter;
   "Lack of effect of X on…" probe studies weren't caught; papers about other drugs that only
   mention a v1 drug in the abstract.
2. **The new title rule wrongly dropped about 13% relevant papers** (titles saying "MTX",
   "platelet reactivity", "antidepressants"); explicit context words added.
3. Comments, animal studies in the title, lab-method papers and a wider probe pattern added in
   one last round; tuning stopped there to avoid fitting the samples.
4. Two of my own test mistakes (a test whose title already contained the drug and gene; a `sed`
   edit that didn't apply), both caught by running the tests.

**Open:** below the spec's 500 (decision for Persons A and B); guideline full text needs its own
source (CPIC guidelines in PMC are author manuscripts, not open-access licensed).

---

## Step 6c: Paper full text, licences, chunks, embeddings (2026-11-11)

**Built:** [`ingest/pmc.py`](../src/drug_safety/ingest/pmc.py) (BioC fetch, licence policy,
sections), [`rag/chunk_papers.py`](../src/drug_safety/rag/chunk_papers.py), the store generalised to
several sources, migration `0002` (PMID, DOI, journal), and a **changed retrieval selection rule**.

**Result:** 445 selected → 440 fetched (5 not yet in BioC) → **344 full text** (CC BY / BY-SA /
CC0: 228; non-commercial BY-NC / BY-NC-SA: 116) and 96 metadata only (80 no-derivatives, 15 without a
CC licence, 1 unknown). **7,551 paper chunks** loaded beside the 998 label chunks.
**Gold: 26/26 through the SQL search** across all 8,549 chunks; 14 cases also get a relevant paper.

**How it was checked:**

| Check | Result |
|---|---|
| `make verify-papers` (each licence decision and section re-derived from the raw file) | Pass |
| `make verify-paper-chunks` (limits, tags, reachability, 8-word-window coverage, reproducible) | Pass |
| `make verify-store` (both sources; vectors; gold through SQL; SQL = in-memory) | 10/10 pass |
| Trial run on the first 193 papers before the full fetch finished | Found 3 chunker problems early |
| Deleting a real chunk makes the coverage check fail | Pass (test) |
| Read the paper returned for each of the 25 gold cases | Led to the distance cut-off; no irrelevant paper remains |
| 161 tests in total | Pass |

**Problems found while checking, and fixed:**

1. **BioC error pages with HTTP 200** saved as papers (5); the fetcher now requires valid BioC JSON.
2. **"CC0" became "CC"** in licence normalisation (caught by a test): public-domain papers would have
   been wrongly excluded.
3. **A letter to the editor** with no abstract; new selection rule plus an offline `reclassify`.
4. Trial chunking: **chunks up to 512 tokens** (re-split until they fit); **521 chunks unreachable**
   by the drug filter (paper drugs and genes added to the context line); table rows reported as lost
   when they were only split (check rebuilt with 8-word windows; I fixed my own check three times:
   short passages, chunk boundaries, overlaps).
5. Reference lists stored as "tables" (184 chunks); dropped at parse time.
6. **Loading papers dropped gold from 26/26 to 20/26**: papers crowded label evidence out of the
   spec's closest-10. Four selection rules measured; tier-balanced chosen (26/26).
7. **Irrelevant supporting papers for lab and lifestyle flags**; a distance cut-off (0.235) removes all
   of them while keeping the relevant ones. Set on the same cases it was judged on, so recheck later.

**Open:** 344 chunked papers vs the spec's 500; whether no-derivatives papers may be chunked;
non-commercial papers if the project becomes commercial; the retrieval rule's difference from the
spec's wording (both for Person A); recheck the 0.235 cut-off on real flags.

---

## Step 7: Retriever (2026-11-11)

**Built:** [`rag/retriever.py`](../src/drug_safety/rag/retriever.py) (`retrieve`, `select`,
`build_context`, `PgRepository`, `InMemoryRepository`, LangChain wrapper),
[`rag/eval_flags.py`](../src/drug_safety/rag/eval_flags.py) (`make eval-flags`), and the 26 gold cases
as engine-style Flags ([`tests/gold/retrieval_flags.yaml`](../tests/gold/retrieval_flags.yaml)).

**Result:** **26/26** gold flags find their expected label passage through the real path (Flag →
`query_text()`/`drug_ids()` → PostgreSQL); 14 also get a relevant paper; no lab flag gets one; the
worked example fits 1,500 tokens with every expected passage kept.

**How it was checked:**

| Check | Result |
|---|---|
| Moving the selection rule into the retriever changed no result (26/26; SQL = in-memory; same 14 papers) | Identical |
| `make eval-flags` | Pass |
| Read the supporting paper for every flag (with Flag-built queries) | Found 1 irrelevant one → lab-flag rule; now all on topic |
| Context trimming keeps the expected passage at 1,500 / 2,500 / 4,000 tokens | Yes (now asserted) |
| 9 retriever unit tests + a PostgreSQL repository test | Pass |
| Three deliberate breaks to the retriever | Each caught by its test |
| 170 tests in total; `verify-store` | Pass |

**Found and fixed:** with engine-style queries an irrelevant paper (atorvastatin + ALT, distance 0.231)
slipped under the 0.235 cut-off. Lab flags now get no supporting papers, because no lab literature was
collected; the threshold wasn't nudged again on the same cases.

**Open:** recheck the cut-off and the lab-flag rule on Person A's real flags (evaluation step).

---

## Step 8: LLM layer and validator (2026-11-11)

**Decided:** the GPU is Google Colab for now. Everything that doesn't need a GPU was built and
checked on the Mac; [`notebooks/colab_qwen_vllm.ipynb`](../notebooks/colab_qwen_vllm.ipynb) serves
Qwen with vLLM and runs the evaluation there.

**Built:** [`src/drug_safety/llm/`](../src/drug_safety/llm/README.md):
- the prompt (spec prompt rules, each flag with only its own evidence, no patient ID);
- the validator (spec rules 1–6, plus rule 7: no invented numbers);
- the drug-name finder;
- the template fallback;
- the OpenAI-compatible client;
- the explain loop (one retry with errors, then the template);
- `explain_patient` for Person A's API;
- the evaluation runner.

Also [`ingest/rxnorm_names.py`](../src/drug_safety/ingest/rxnorm_names.py) and
[`scope/drug_name_ignore.yaml`](../scope/drug_name_ignore.yaml). Test patients:
[`tests/gold/llm_cases.yaml`](../tests/gold/llm_cases.yaml). There are 10 synthetic patients using all 26 gold
flags; patient 1 is the spec's worked example.

**How it was checked:**

| Check | Result |
|---|---|
| The spec's own p19 example answer passes the validator | Pass |
| 18 answers that each break one rule are rejected by exactly that rule | Pass |
| 10 normal descriptions are not mistaken for orders | Pass |
| Five deliberate breaks to the validator | Each caught by a test |
| Drug finder run over all 8,549 evidence chunks; all 444 names it reported read by hand | 34 non-drug terms found (body chemistry, foods, abbreviation brands) and added to the ignore list |
| `make verify-drug-names` | 9/9 |
| `make verify-llm`: frozen evidence for 10 patients, prompt size with Qwen's own tokenizer, fallback valid | 7/7 |
| Colab bundle run in a clean environment (only light packages) against a stand-in OpenAI server over HTTP | Wrong API key refused; an invented drug caught and fixed by the retry; results written |
| Server down | Template report, "model server could not be reached" notice, no retry |
| Schema-guided mode sends the Report JSON schema, temperature 0.1, max tokens 1500 | Checked on the wire |
| `make check` | Lint clean, 218 tests pass |

**Found and fixed:**
1. The retry didn't fit an 8,192-token window (worst ≈ 8,400). vLLM now starts with 16,384.
2. The template report failed rule 6: a colon split made "lower warfarin dose requirement" read as an order.
3. The full RxNorm list made 623 ordinary words count as drugs. Switched to the prescribable subset and reviewed what was left.
4. A settings field shadowed a pydantic method (renamed).

**Not yet known (needs the Colab run):** whether Qwen passes the validator on its first try, the
fallback rate, latency, and the manual faithfulness check (`review.md`). The notebook's vLLM
start on a T4 is also untested. Current vLLM supports the T4 (compute capability 7.5), and the
notebook uses float16, AWQ, and the FlashInfer sampler switched off. If it fails, the notebook
prints the log.

**Open:** rule 7 and rule 4's strictness, for Person A (README open decisions). Use synthetic
patients only on Colab.

## Step 9: Streamlit UI (2026-11-11)

**Built:** the spec p17 screen, `src/drug_safety/ui/` ([README](../src/drug_safety/ui/README.md)).
- **Input:** prescriptions, labs, genotypes, smoking, alcohol, diet and meals; a JSON upload; 10
  synthetic examples.
- **Output:** a fixed banner, the flag table sorted by severity, data-gap notes, the summary and
  suggestions, then each explanation with numbered expandable evidence and source links.
- **API:** it talks only to `API_URL`, and every answer is checked against `AssessResponse`.

Person A's API doesn't exist yet, so `ui/stub_api.py` stands in for it. It has the same
endpoints and contract and serves the frozen flags and evidence for the 10 examples. Reports
come from the real LLM layer. Any other patient gets a 404 saying so; the stand-in never guesses.

**How it was checked:**

| Check | Result |
|---|---|
| `make verify-ui`: all 10 examples through the stand-in over HTTP and the real screen (headless) | 10/10 |
| `pytest tests/ui`: screen, form, client, stand-in, display logic | 59 passed |
| 7 deliberate breaks (escaping off, banner removed, stand-in guessing, uncited evidence hidden, contract check off, empty rows kept, data-gap notes missing) | Each caught |
| Real Chrome run of the worked example, screenshots read | Layout as spec; 1 bug found (below) |
| Evidence source links (DailyMed, PubMed Central) | HTTP 200 |
| `make check` | Lint clean, 277 tests pass |

**Found and fixed:**
1. The "fixed" banner scrolled away in a real browser. Streamlit 1.67 wraps each container in its
   own box, so the sticky rule had nothing to stick to. The fix targets the wrapper, and
   Streamlit is pinned to 1.67 or later. Only the browser run could have shown this.
2. After Assess, the result appeared below the long form. The form now folds away when a result
   exists.
3. Model and evidence text could have carried markdown links or images into the page. All API
   text is now escaped, with a test.

**Open:** joining Person A's real API (spec build step 7). The `/health` shape is a suggestion to
A. The data gaps shown come from the stand-in's guess until A's engine provides them.

## Step 8b: First real Qwen run and fixes (2026-11-11)

**Run:** Colab, NVIDIA L4, vLLM 0.33.0, Qwen 2.5 7B Instruct (full precision), prompt `explain-v1`,
plain mode, 10 synthetic patients. The guided run was not done. Every answer was read by hand.

| | Result |
|---|---|
| Accepted first try / after retry / template | 5 / 2 / 3 of 10 |
| Invented drugs or numbers | 0 of 15 answers |
| Seconds per answer | 55 mean, 83 max |

**Found:**
1. **Too loose:** dose instructions in explanations ("Dose adjustments are necessary") passed, so
   p09 was accepted wrongly. "Consider alternative antiplatelet therapy" passed too.
2. **Too strict:** "Consider reviewing … potential dosage adjustments" and "may require a lower
   dose" were rejected, causing 2 needless fallbacks.
3. **Missing flags:** Qwen left out a flag on 4 of 10 first tries.
4. **Retries:** the retry repeated rejected wording, because the error message didn't say what to
   change.
5. **Speed:** a third of each answer was copied chunk IDs.

**Fixed (`explain-v2`):**
- Rule 6 now covers a dose change stated as needed, in every field.
- Suggestions must start "Consider" plus a review verb, and a dose change counts only as the action.
- The prompt ends with the exact list of flag IDs.
- Retry messages quote the problem and say how to rewrite it.
- Evidence appears as `E1`, `E2`…, mapped back to chunk IDs in code.

**How it was checked:**

| Check | Result |
|---|---|
| The 15 real answers as permanent tests, each with a reviewer's verdict | Pass (they failed before the fix on p01, p02, p06, p09) |
| The same answers with the full RxNorm list | Same verdicts; 4/10 first try, 7/10 by retry |
| 5 deliberate breaks to the new code | Each caught |
| Answer length with labels (Qwen tokenizer, the 15 answers) | 54% fewer tokens |
| `make verify-llm` | 7/7; worst retry prompt 6,286 tokens (was 6,928) |
| `make check` | Lint clean, 291 tests pass |

The notebook's evaluation cell now prints progress live, with errors included.

**Pending:** one Colab run of `explain-v2` (plain and guided, about 15–30 min on an L4) to measure
the first-try rate, fallback rate and speed with the new prompt. Nothing else waits for it: with
no model, reports use the template.

## Step 6d: CPIC guideline text (2026-11-11)

**Licence first:**
- **CPIC is CC0 1.0** (CPIC's own `LICENSE.md`, changed 2026-03-09); attribution is requested and
  the logo may not be reused.
- **DPWG via ClinPGx is CC BY-SA 4.0** plus a share-alike agreement and "no sale for private or
  commercial use" (`LICENSE.txt` in the download, 2026-11-05). Not loaded: question E5 for Person A.

**Built:**
- `ingest/cpic.py` fetches CPIC's recommendations for the v1 drugs: 4 API requests, raw responses
  kept.
- It keeps rows where a v1 gene has a non-normal phenotype, and merges rows that repeat the same
  text per activity score.
- `rag/chunk_cpic.py` makes one chunk per recommendation, tagged with its own drug only:
  **171 chunks, 8 guidelines, 14 drugs**, loaded as source `cpic` (tier 1).
- Every v1 level-A pair is covered except warfarin, whose CPIC guideline is a dosing algorithm.

**Found while checking:**
1. **Wrong phenotypes.** The embedding can't tell phenotypes apart. With CPIC loaded, 3 of 8 gene
   flags got advice for the wrong phenotype:
   - codeine *ultrarapid* got *poor*-metabolizer advice;
   - SLCO1B1 poor function got *increased* function;
   - CYP2C9 poor got *intermediate*.

   The gold check still passed, because it looks for label passages. Fix: a CPIC recommendation
   counts only for a gene flag with the same phenotype (any other gene in it must be normal), and
   at most one per flag, so the label keeps the other tier-1 slot. That matches the spec's worked
   example.
2. **A spot check that was too broad.** For neurovascular indications CPIC says "an alternative
   P2Y12 inhibitor", not "prasugrel or ticagrelor". It is now checked per population.
3. **Drug names in CPIC text.** 6 of the 8 CPIC chunks for the test patients name alternative
   drugs, which validator rule 4 would reject if Qwen repeated them. The prompt now says to write
   "an alternative antiplatelet". The next Colab run measures the effect.

**How it was checked:**

| Check | Result |
|---|---|
| `make verify-cpic` | 17/17 |
| `make eval-flags`: guideline or label passage | 26/26 (label 25/26; CPIC for the right phenotype 8/9 gene flags) |
| Every retrieved CPIC chunk's phenotype read by hand against its flag | All match |
| `make verify-store` (labels, papers and CPIC) | Pass; SQL = in-memory |
| Tests for the row filter, chunk text, tagging and retriever rule; 5 deliberate breaks | Each caught (one break went uncaught at first, and its test was strengthened) |
| UI: CPIC passages link to their ClinPGx guideline page | HTTP 200; `make verify-ui` 10/10 |
| LLM cases rebuilt with CPIC evidence; `make verify-llm` | 7/7 (worst retry 6,540 tokens) |
| `make check` | Lint clean, 298 tests pass |

**Open:** DPWG (E5), CPIC/ClinPGx licence wording (E6), the retrieval rule (F5) and linking rules
to CPIC IDs (F6), all for Person A.

## Step 10: Evaluation (2026-11-11)

**Built:** `make eval-report`, Person B's report as the spec defines it, written to
`data/outputs/<date>_evaluation/report.md` and `metrics.json`
([evaluation README](../src/drug_safety/evaluation/README.md)). It never calls the model. Each
LLM run is re-judged by today's validator, and runs on an older prompt are marked.

**Results today:**

| Spec measure | Result |
|---|---|
| Retrieval hit rate (expected guideline or label chunk in the top 3) | **26/26** (every flag type complete; label 25/26, CPIC 8/9 gene flags) |
| LLM faithfulness (accepted on the first try) | Run 1, older prompt: 5/10; re-judged by today's validator: 4/10. **Current prompt: no run yet** |
| Fallback rate | Run 1: 3/10 (today's validator: 3/10 still fall back) |
| Latency per patient without the LLM | **0.09 s** mean, 0.13 s max (warm); loading the embedding model at start-up 12.7 s |
| Latency per patient with the LLM | About 83 s (run 1, older prompt; the new prompt's answers are about half as long) |
| Manual check of about 10 reports | Run 1: 15 answers read, **no invented claims**; two problems no rule catches (`docs/llm_manual_review.md`). Still to be confirmed by a person |

**Definition of done, B's items:**

| Item | Status |
|---|---|
| 2. At least 500 papers | **FAIL**: 344 (decision F1) |
| 4. Gold flags have their expected chunk in the top 3 | **PASS** |
| 5. LLM output adds nothing beyond the flags and evidence | **PENDING**: needs a run of the current prompt |
| 6. One patient through the API and the screen | **PENDING**: works with the stand-in API |
| 7. README, environment variables and stand-alone tests for every module | **PASS** |
| 7. `docker compose up` | **PENDING**: the screen's container image isn't built yet |

**Found while checking:**
1. **READMEs.** The `contracts` and `rag` READMEs had no environment-variable section, which the
   spec requires; both are added. The report now checks it, and a test confirms the check fails on
   a missing section.
2. **First-call timing.** My first version reported the first retrieval call as "loading the
   model", but the model was already loaded. It now times the model load itself.
3. **Re-judging old runs.** Old runs couldn't be re-judged without knowing their inputs. Each
   case file now saves its flags, evidence and summary; run 1 is re-judged through its saved test
   fixture, and only when the answers match exactly.
4. **An empty `guided` folder** from my own test runs was found and removed. The report ignores
   folders without results.

**How it was checked:**
- The report's numbers agree with the separate checks: `eval-flags` 26/26, re-judging 4/10 and
  7/10 matches `test_real_answers`, and the module test counts add up to `make check`'s total.
- The LLM evaluation ran end to end against a stand-in OpenAI server: answers cite `E1`, reports
  hold the real chunk IDs, the inputs are saved, and the re-judge works.
- `tests/evaluation`: 4 tests. `make check`: lint clean, **302 tests pass**.

## Step 9b: The screen's container image (2026-11-12)

**Built:** `docker/ui.Dockerfile` (Python 3.12 slim, the 6 pinned packages, only the screen's
files, a non-root user, a health check), `.dockerignore`, and `docker/compose.ui.yml` (the `ui`
entry proposed for Person A's compose file, question D6). See [docker/README.md](../docker/README.md).

**How it was checked:**
- The container was built and reported `healthy`, running as uid 10001.
- From inside the container it reached the stand-in API, and `psycopg` was absent, as intended.
- The worked example ran in Chrome against the container: fixed banner, flag table, and the CPIC
  passage with its ClinPGx link.

**Found while checking:**
1. **API address.** The sidebar showed it as `http://host\.docker\.internal:8080`, because
   escaping shows literally inside a code span; the Mac runs used `localhost`. Fixed, with a test.
2. **Repeated title.** CPIC passages repeated the guideline title in their heading, both on screen
   and in Qwen's prompt. The heading is now the phenotype and population ("CYP2C19 Poor
   Metabolizer · CVI ACS PCI"). The chunks were reloaded and every downstream check rerun:
   retrieval 26/26, store, LLM cases 7/7, screen 10/10.
3. The Deploy toolbar button is hidden in the image.

## Contracts 0.1.0 (2026-11-14)

**Decided:** Person A answered all 44 questions (`docs/person_a_responses_2026-11-14.json`). 43
were settled: 42 agreed with B's proposals, and E1 is a decision (keep DDInter and the CC BY-NC
papers, so the project stays non-commercial). A7 (alcohol amount) is still open; A10 (the HLA-B
phenotype, raised by E3) is new.

**Built:**
- The contracts are now `0.1.0`, with the agreed optional fields: `Prescription.status`
  (new/ongoing), `Patient.weight_kg` and `Patient.conditions` (BEN). JSON schemas regenerated.
- The screen has inputs for all three: a status column, a weight box and a conditions list.
- Qwen's patient summary mentions them when they're given.
- The evaluation report shows the paper count as an accepted deviation (F1), and
  `docs/sources.md` records E1.

**How it was checked:**
- Contract tests: valid values accepted, wrong ones rejected, and older patient files still load.
- Form tests: a round trip through the form, and one readable message per bad value.
- A headless screen test: a weight typed in reaches the API request. Removing that wiring on
  purpose made it fail.
- Prompt tests: the summary is unchanged when the fields are empty.
- `make verify-llm` 7/7; the 10 test patients' prompts are unchanged.

## DPWG, own-label rule, French acenocoumarol label (2026-11-14)

All three follow Person A's answers (E2, E5).

**DPWG (`ingest/dpwg.py`, `rag/chunk_dpwg.py`):** ClinPGx's DPWG annotations, only for pairs CPIC
doesn't cover. Reading them showed that only 3 of the 5 expected pairs have advice:
- acenocoumarol −1639 AA: 50%;
- warfarin CYP2C9 intermediate 65%, poor 20%;
- warfarin −1639 AA: 60%.

Acenocoumarol + CYP2C9 and clozapine + CYP2D6 are "no action", so they get no chunks, and question
C7 asks Person A not to flag them. The result is **4 chunks**, and `make verify-dpwg` gives 14/14.
ClinPGx writes the same VKORC1 genotype two ways; both are stored as "−1639 AA", and A10 now asks
how VKORC1 and HLA-B flags state their result.

**Own label first (retriever):** with DPWG loaded, the warfarin flag's one label slot held a
**Celebrex** passage that only mentions warfarin. In the 2 main tier-1 slots, the flag drug's own
label now comes first.
- My first version also applied the rule to the fill-in third slot. That swapped a methotrexate
  passage for an unrelated antiretroviral one (methotrexate + pantoprazole), so the fill-in slot
  went back to plain distance.
- Measured on all 26 gold flags: only warfarin changed. Tests catch both breaks.

**Gold:** 26/26, label 25/26, guideline recommendation **9/9 gene flags** (warfarin now has DPWG).

**French acenocoumarol label (`ingest/bdpm.py`, `rag/chunk_bdpm.py`):**
- 13 passages that v1 flags can need, translated by Claude.
- The French is checked word for word against the stored page.
- **None is used until a person who reads French reviews it** (question E7).
- Corrections to the earlier notes: the licence is **Licence Ouverte 2.0** (not 1.0; it allows
  translation), the label was **updated 20/06/2025** (not 2018), and alcohol is mentioned only in
  the patient leaflet.

**Also found and fixed:**
- **Loading an empty source crashed** (the embedder can't take an empty list). The store now skips
  embedding; a database test uses an embedder that fails if called, and fails without the fix.
- **My first edit to that line also changed a second, more indented occurrence** and broke the
  file. Caught by lint and corrected.

**How it was checked:**

| Check | Result |
|---|---|
| `make verify-dpwg` | 14/14 |
| `make verify-bdpm` | 10/10; 0 of 13 translations reviewed |
| `make verify-cpic` | Pass |
| `make eval-flags` | 26/26 (label 25/26, guideline 9/9) |
| `make verify-store` (5 sources) | Pass; SQL = in-memory |
| `make check` | Lint clean, 318+ tests pass |

## HLA-B added to v1 (2026-11-14)

**Decided:** Person A agreed to add HLA-B (E3).

**Built:**
- **Scope:** `scope/v1.yaml` now has 6 genes, and the name list includes "HLA-B" and "HLA B".
- **CPIC:** CPIC states HLA-B as an allele result ("HLA-B\*58:01 positive"), in `allelestatus`,
  which is now read for genes with no phenotype. "Negative" and "No HLA-B Result" mean no action,
  and a negative HLA-B in a phenytoin row doesn't stop a CYP2C9 flag from matching.
- **Result:** CPIC grew to **184 recommendations in 9 guidelines** (allopurinol is new), and all
  **19 level-A pairs** are covered except warfarin.
- **Gold:** two HLA-B flags were added (allopurinol + HLA-B\*58:01, phenytoin + HLA-B\*15:02),
  with their expected label passages.
- **Screen:** the genotype column shows an HLA-B example.

**Found while checking:**
1. Both no-action checks used `.match`, which only looks at the start of the text, so
   "HLA-B\*58:01 **negative**" counted as actionable, in the parser and in the retriever. My own
   spot check exposed the parser one; the retriever one was hidden because my check used
   `.search`. Both are fixed, and a test fails for each when the bug is put back.
2. Headings repeated the gene name ("HLA-B HLA-B\*15:02 positive"); fixed.
3. `tests/test_setup.py` pinned the spec's 5 genes. It now states the spec's 5 plus HLA-B, by
   decision.

**How it was checked:**

| Check | Result |
|---|---|
| `make verify-cpic` | Pass (19 level-A pairs) |
| `make eval-flags` | **28/28** (label 27/28, guideline recommendation **11/11** gene flags; both HLA-B flags get label + CPIC) |
| `make verify-store` (labels, papers, CPIC, DPWG reloaded) | 28/28 via SQL; SQL = in-memory |
| `make eval-retrieval` | 28/28 for both embedding models |
| `make verify-llm`, `make verify-ui` | 7/7, 10/10 |
| `make check` | Lint clean, **320 tests pass** |

**Open for Person A:** B7 (add HLA-B to his `v1.yaml`), and A10 (how a HLA-B flag states its
result; the screen asks for `*58:01 positive`).

