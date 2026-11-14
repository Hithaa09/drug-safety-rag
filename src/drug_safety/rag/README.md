# rag: chunking, embeddings, retriever

**Owner:** Person B

| Part | File | Status |
|---|---|---|
| Label chunking | [`chunking.py`](chunking.py) | **Done**, verified 2026-11-11 |
| Drug and gene tagging | [`tagging.py`](tagging.py) | **Done** (temporary dictionary until Person A's `drug_alias`) |
| Paper chunking | [`chunk_papers.py`](chunk_papers.py) | **Done**: 7,551 chunks from 344 papers |
| CPIC guideline chunking | [`chunk_cpic.py`](chunk_cpic.py) | **Done**: 171 chunks, one per recommendation (step 6d; see `ingest/README.md`) |
| Embedding model choice | [`embeddings.py`](embeddings.py), [`eval_retrieval.py`](eval_retrieval.py) | **Done**: `bge-base-en-v1.5` kept (26/26 gold cases) |
| Embeddings in the database | [`store.py`](store.py), [`../db.py`](../db.py) | **Done**: 998 chunks in `kb.evidence_chunks`, 26/26 gold cases via SQL |
| Retrieval selection rule | `eval_retrieval.py` (`assemble`), `store.py` (`search_sql`) | **Changed in step 6c**: tier-balanced (see below) |
| **Retriever** (`Flag` in, cited evidence out) | [`retriever.py`](retriever.py), [`eval_flags.py`](eval_flags.py) | **Done**: 26/26 gold flags through the contract and PostgreSQL |

---

# Label chunking

## What it does

It takes the 24 parsed FDA labels from the DailyMed loader and cuts them into **998 evidence
chunks**: short passages that will be embedded and searched, so each flag can cite the exact
label text behind it.

1. **Keeps only sections that can be evidence for a flag:** contraindications, warnings,
   interactions, dosing, special populations (kidney, liver, pregnancy), clinical pharmacology
   (including pharmacogenomics), adverse reactions, overdose, and the OTC warning blocks.
   It **drops** patient leaflets (they repeat the prescribing text), chemistry, packaging,
   animal studies, reference lists and clinical-trial narratives. The full list, with a reason
   for each, is in `KEEP_SECTIONS` and `DROP_SECTIONS` in `chunking.py`.
2. **Packs small subsections together** (for example 4.1 and 4.2 Contraindications) up to about
   400 tokens, each keeping its own heading. Two different top-level sections are never mixed in
   one chunk.
3. **Splits long subsections** at paragraph, then line, then sentence boundaries. Every piece
   repeats the subsection heading.
4. **Splits long tables by rows.** Every piece repeats the table title (marked "(continued)") and
   the header row, so no chunk holds numbers without their column names.
5. **Starts every chunk with a context line**, e.g.
   `clopidogrel | PLAVIX (CLOPIDOGREL) TABLET, FILM COATED | 7 DRUG INTERACTIONS`.
6. **Tags every chunk** with the v1 drugs and genes it mentions, plus the label's own drug, so
   the retriever can filter by drug before searching (spec, RAG layer step 2).
7. **Tags drug classes separately.** If the text names a class ("NSAIDs", "other SSRIs"), the
   chunk gets `drug_classes` (e.g. `["nsaid"]`) and `class_drugs`: the v1 drugs in that class,
   from the RxClass step (e.g. aspirin, celecoxib, diclofenac, ibuprofen). These are kept apart
   from `drugs`, so the drug filter stays exact; the retriever uses classes only as a fallback.

Sizes are measured with the **embedding model's own tokenizer** (`BAAI/bge-base-en-v1.5`). That
model reads at most 512 tokens and silently ignores the rest, so the hard limit is 480 per chunk.

## Quick reference

```bash
make chunk-labels    # build chunks from the parsed labels (about 10 s)
make verify-chunks   # offline: 25 checks, ends with RESULT: PASS or FAIL
make test            # unit tests (offline)
```

Order: `make ingest-dailymed`, `make fetch-classes`, then `make chunk-labels`. If the labels change (re-pinned label, or a
parser change), run `chunk-labels` again; `verify-chunks` tells you when that's needed.

## How to know it worked (no code review needed)

Run `make verify-chunks`. Each line says PASS or FAIL:

| Check | What it proves |
|---|---|
| every loaded label has chunks / no chunks from unknown labels | All 24 labels were chunked, and nothing else |
| chunk_ids unique / format | IDs are stable and follow `{doc_id}:{section}:NNN` |
| fits the embedding model (<= 512 tokens) | No chunk will be cut off when embedded (re-counted with the real tokenizer) |
| within chunk limit / stored token counts are correct | Sizes are as designed and the stored numbers are true |
| only kept sections chunked | No patient leaflet, packaging or other dropped text slipped in |
| every chunk tagged with its label's drug / tags are v1 only / tags match the dictionaries | The drug filter in retrieval will work; drug, gene and class tags are current |
| **no text lost from kept sections** | Every sentence of every kept section appears in a chunk |
| **table rows always come with their header row** | No chunk has orphaned table numbers |
| up to date | Re-chunking today gives identical output |
| gold: 11 spot checks, gold class: 3 | The evidence the spec's examples rely on is present and tagged correctly (below) |

Gold spot checks (each must find a chunk from that drug's own label):

| Drug | Must contain | Must be tagged |
|---|---|---|
| clopidogrel | "poor metabolizer" | CYP2C19 |
| clopidogrel | "omeprazole" | omeprazole |
| warfarin | "VKORC1", "CYP2C9", "vitamin K" | VKORC1, CYP2C9 |
| codeine | "ultra-rapid" | CYP2D6 |
| simvastatin | "OATP1B1" | SLCO1B1 |
| metformin | "eGFR" | — |
| clozapine, theophylline | "smok" (smoking) | — |
| atorvastatin | "grapefruit" | — |

Class spot checks: the Plavix label's "NSAIDs", "SSRIs" and "Opioids" passages must reach
ibuprofen, sertraline and codeine through `class_drugs`, without those drugs being named in the chunk.

Last real run: **28/28 checks passed**.

## Inputs and outputs

| | Path |
|---|---|
| In | `data/processed/dailymed/<setid>.json` and `_manifest.json` (from the DailyMed loader) |
| In | `scope/drug_aliases.yaml` (tagging dictionary) |
| Out | `data/processed/chunks/dailymed.jsonl`: one chunk per line, the future `kb.evidence_chunks` rows |
| Out | `data/processed/chunks/dailymed_manifest.json`: per label, chunk count, token range, sections kept and dropped, genes tagged |

One chunk (shortened):

```json
{
  "chunk_id": "dailymed:de8b0b67-eb25-4684-83b5-7ad785314227:drug_interactions:002",
  "doc_id": "dailymed:de8b0b67-eb25-4684-83b5-7ad785314227",
  "source": "dailymed",
  "tier": 1,
  "section": "drug_interactions",
  "headings": [["7 DRUG INTERACTIONS", "7.2 CYP2C19 Inhibitors"]],
  "text": "clopidogrel | PLAVIX (CLOPIDOGREL) TABLET, FILM COATED | 7 DRUG INTERACTIONS\n\n7.2 CYP2C19 Inhibitors\nClopidogrel is metabolized to its active metabolite in part by CYP2C19. ... Avoid concomitant use of Plavix with omeprazole or esomeprazole. ...",
  "drugs": ["clopidogrel", "omeprazole", "pantoprazole"],
  "genes": ["CYP2C19"],
  "drug_classes": ["ppi"],
  "class_drugs": ["omeprazole", "pantoprazole"],
  "token_count": 370,
  "source_version": "5",
  "license": "DailyMed (NLM) SPL; see DailyMed terms of service",
  "chunker_version": "labels-v2",
  "load_id": "chunks-dailymed-20261111T...",
  "embedding": null,
  "embedding_model": null
}
```

`embedding` and `embedding_model` stay empty until the embedding step.

## Numbers from the real run

- 998 chunks from 24 labels; tokens: min 31, median 328, max 477.
- 121 chunks carry a gene tag; 134 mention another v1 drug besides their own; 319 name a drug class.
- 49 chunks are under 100 tokens: short sections such as one-line contraindications or OTC
  warnings, kept whole on purpose.

## Tagging dictionary (`scope/drug_aliases.yaml`)

- Whole-word, case-insensitive: "omeprazole" doesn't match "esomeprazole", and "citalopram"
  doesn't match "escitalopram".
- Brand names map to the ingredient (Plavix to clopidogrel, Tylenol and acetaminophen to paracetamol).
- Genes include older label spellings ("cytochrome P450 2D6", "P450 isoenzyme 2D6") and the
  SLCO1B1 protein name OATP1B1.
- **Temporary:** it will be replaced by Person A's `drug_alias` table. **To add a name:** edit
  the YAML, then run `make chunk-labels` and `make verify-chunks`.

## Known limitations

| Item | Effect |
|---|---|
| Overlap | Neighbouring pieces overlap only when a paragraph itself had to be split. Whole paragraphs aren't repeated |
| Class words | Only the classes in `scope/drug_classes.yaml` are recognised. Broad words such as "anticoagulants" aren't, because they cover drugs outside v1 |
| Chunk IDs and label versions | IDs are stable for a given label version. If a label is re-pinned or updated, IDs in that label can shift, and curated rules that link to them must be rechecked (the curation step will have its own check) |
| Clinical-trial narratives dropped | `clinical_studies` text isn't searchable. Add it to `KEEP_SECTIONS` if retrieval evaluation shows it's needed |

## Troubleshooting

| Symptom | Fix |
|---|---|
| `unclassified section types [...]` | A label has a section type never seen before. Add it to `KEEP_SECTIONS` or `DROP_SECTIONS` in `chunking.py`, with a reason |
| verify: "up to date" FAIL | Labels, dictionary or chunker changed. Run `make chunk-labels` |
| verify: "tags match the current dictionary" FAIL | You edited `drug_aliases.yaml`. Run `make chunk-labels` |
| First run is slow, or fails offline | It downloads the tokenizer (about 700 KB) once from Hugging Face; afterwards it's cached |

## Tests

`pytest tests/rag` (offline; tokens counted as words).

| Test | Proves |
|---|---|
| `test_small_subsections_packed_with_headings` | Small subsections share a chunk, each with its heading, plus the context line |
| `test_top_level_sections_never_mixed_and_numbered_separately` | Sections never mix; IDs count per section |
| `test_packing_stops_near_target` | Packing respects the 400-token target |
| `test_long_subsection_split_with_heading_repeated` | Long text is split under the limit, and every piece keeps its heading |
| `test_long_table_split_repeats_caption_and_header` | Long tables keep their title and header in every piece; no row lost; notes at the end |
| `test_dropped_and_unknown_sections` | Dropped sections are skipped; an unknown section type stops the run |
| `test_tags_include_own_drug_and_mentions` | Drug and gene tags are filled |
| `test_class_mentions_tagged_separately_from_drugs` | "NSAIDs" fills `drug_classes` and `class_drugs` but not `drugs` |
| `test_verify_*` | Verify passes on good output, and catches lost text, a missing table header and an oversized chunk |
| `test_tagging.py` | Whole-word matching, brand names, older gene spellings, class phrases (and "antiplatelet activity" not counted as a class); the dictionary matches the scope |

## Design decisions

- **Real tokenizer, not a word-count estimate:** the embedding model drops text past 512 tokens
  without warning, so sizes must be exact.
- **Section-aware, not fixed-length** (spec): chunk boundaries follow the label's own headings.
- **A context line in every chunk:** the embedding and the LLM both see which drug and section a
  passage comes from, even for a passage such as "Avoid concomitant use".
- **An unknown section type stops the run:** a new kind of section can't silently be dropped or
  silently included.
- **JSONL output, no database yet:** this doesn't depend on Person A's schema; loading into
  `kb.evidence_chunks` happens with embeddings.

---

# Embedding model choice (retrieval test)

## Why this test exists

Changing the embedding model later means re-embedding every chunk (spec, RAG layer), so the
model was tested on real chunks **before** any bulk load. The spec says to switch to a biomedical
model if retrieval quality is poor.

## How it works

- **Gold cases:** [`tests/gold/retrieval_labels.yaml`](../../../tests/gold/retrieval_labels.yaml)
  holds 26 flags covering all four flag types (9 gene-drug, 5 drug-drug, 7 lab-drug,
  5 lifestyle-drug). For each one it lists the label passages a pharmacist would cite. **They
  were written before any model was run.** One expectation (allopurinol) was corrected
  beforehand, because a check showed no chunk could match its wording.
- **Search** copies the spec's retriever in memory: filter by drug, cosine top 10, re-sort by
  tier then distance, keep 3. A case passes if any of the 3 matches.
- **Baseline:** for comparison, the chance that 3 random chunks from the filtered pool would pass.

## Result (2026-11-11)

| Model | Query instruction | hit@3 | MRR | Random order |
|---|---|---|---|---|
| `BAAI/bge-base-en-v1.5` | no | **26/26** | **0.981** | 26% |
| `BAAI/bge-base-en-v1.5` | yes ("Represent this sentence...") | 26/26 | 0.962 | 26% |

- Every flag type 100%. The right passage was first in 25 of 26 cases. In the remaining one
  (celecoxib), the first result was "12.5 Pharmacogenomics", which is clinically right but
  stricter than the gold wording; the gold set wasn't changed after seeing results.
- **Harder test without the drug filter** (all 998 chunks): 25/26. The one miss was an
  atorvastatin grapefruit question answered by simvastatin's grapefruit passage, which shows why
  the spec filters by drug first.
- Several first results are packed chunks whose relevant subsection isn't the first heading
  (for example methotrexate 8.4–8.6, where 8.6 is Renal Impairment). That's expected and correct.

**Decision:** keep `BAAI/bge-base-en-v1.5` (768 dimensions), **without** the query instruction.

## Commands

```bash
make eval-retrieval    # about 2 minutes the first time (model download + embedding); cached afterwards
```

It prints hit@3, MRR and the baseline for each mode, lists any misses with what was returned,
saves every case's ranking to `data/outputs/<date>_retrieval_eval/`, and ends with
`RESULT: PASS` (every case in the top 3, the spec's bar) or `BELOW TARGET`.

Embeddings are cached in `data/cache/embeddings/<model>.npz`, keyed by the exact chunk text, so
reruns are fast and only changed chunks are re-embedded.

**To add a gold case:** add it to the YAML with `query`, `filter` and `expect`. Before running the
model, check that some chunk can match it, then run `make eval-retrieval`.

## Tests

`tests/rag/test_eval_retrieval.py`: the drug filter is applied before ranking (a closer statin
passage can't answer a clopidogrel flag); higher-tier chunks win within the top 10; heading and
text matching; the random baseline maths; the gold file covers all four flag types and is well formed.

---

# Embeddings in the database

## What it does

`make store-labels` embeds every label chunk (cached, so only new or changed text is embedded)
and writes the labels to `kb.documents` and the chunks with their vectors to
`kb.evidence_chunks`, **in one transaction**:

- documents for the current labels are inserted or updated;
- their chunks are replaced;
- labels that are no longer pinned are removed (their chunks go with them).

If anything fails, the transaction rolls back and the database stays as it was (tested).
Every row carries the run's `load_id`; `remove --load-id ...` deletes exactly one run, as the
spec asks ("reload by ID").

The tables come from [`warehouse_proposal/0001_kb_document_store.sql`](../../../warehouse_proposal/0001_kb_document_store.sql),
a proposal for Person A's warehouse.

## Commands

```bash
make db             # start PostgreSQL + pgvector (Docker Desktop must be running)
make db-migrate     # create the tables (safe to rerun)
make store-labels   # embed + load (about 1 minute; seconds when cached)
make verify-store   # 9 checks; ends with RESULT: PASS or FAIL
.venv/bin/python -m drug_safety.rag.store remove --load-id store-dailymed-...
```

Order: `chunk-labels`, then `store-labels`. Rerun `store-labels` whenever the chunks change;
`verify-store` tells you when that's needed.

## What verify-store checks

| Check | What it proves |
|---|---|
| documents match the loaded labels / chunks match the chunk file | The database holds exactly the current labels and chunks |
| stored text and tags equal the chunk file | Nothing is stale or edited |
| every embedding has 768 dimensions / embedding model is bge-base | Vectors fit the column and come from the chosen model |
| stored vectors equal the embedding of their text | Each vector really belongs to its chunk |
| every document has chunks | No empty documents |
| **gold retrieval via SQL: 26/26 in top 3** | The real pgvector search finds the expected passage for every gold flag |
| **SQL search gives the same top 3 as the in-memory evaluation** | The database search behaves exactly like the tested design |

Last run, 2026-11-11: **9/9 passed.** Search takes about 7 ms per query with no vector index,
so the HNSW index the spec mentions isn't needed yet.

## The search (spec RAG layer)

```sql
SELECT chunk_id, tier, embedding <=> %(qvec)s AS distance
FROM kb.evidence_chunks
WHERE drugs && %(drugs)s
ORDER BY distance
LIMIT 10
```

Then sort by `(tier, distance)` and keep 3 (`search_sql` in `store.py`). The full retriever with
the `Flag` contract comes in step 7.

## Tests

`tests/rag/test_store_db.py` runs on a **throwaway database** (`drugsafety_test`, created and
dropped by the test) with a fake embedder, and is skipped if the database isn't running:
load and reload without duplicates; a label that's no longer pinned is removed; **a failed load
changes nothing** (forced foreign-key error, then counts checked); remove by `load_id`; the SQL
drug filter.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `connection refused` | Start Docker Desktop, then `make db` |
| `relation "kb.evidence_chunks" does not exist` | `make db-migrate` |
| verify: "table is empty" or counts differ | `make store-labels` |
| verify: "stored text and tags equal the chunk file" FAIL | Chunks changed since loading; `make store-labels` |

---

# Paper chunks

`make chunk-papers` / `make verify-paper-chunks`, then `make store-papers`.

Same design as label chunks, with three differences:
- the context line names the paper's drugs and genes (from selection, where its title or abstract had to
  name them), e.g. `paper | clopidogrel · CYP2C19 | <title> (2019) | Results`, so every chunk is
  reachable by the drug filter (before this, 521 of 3,906 trial chunks weren't);
- passages that are too long are split, then **re-checked and split again until each fits**
  (token counts don't add up exactly when pieces are joined; 4 trial chunks reached 512);
- "no text lost" is checked with **8-word windows** that must appear inside a chunk, or across two
  consecutive chunks once the splitter's overlap is removed. Tables have no sentence breaks, so
  the label check (whole sentences) can't tell "split" from "lost". A test deletes one real
  chunk and the check must fail.

Result: 344 papers → 7,551 chunks (median 284 tokens, max 478), all tagged with a drug.

# Retrieval selection: tier-balanced (changed 2026-11-11)

**What changed and why.** The spec says: rank the drug-filtered chunks, take the closest 10,
re-sort by tier, keep 3. With 7,551 paper chunks loaded, papers filled the closest 10 and the label
evidence never reached the tier sort: **gold fell from 26/26 to 20/26** (expected label passages at
ranks 13 to 342). Measured options:

| Selection | Gold label evidence in top 3 | A paper also in top 3 |
|---|---|---|
| Spec as written: closest 10, sort by tier | 20/26 | 10/26 |
| Closest 30 | 23/26 | 6/26 |
| Closest 100 | 23/26 | 3/26 |
| **Best 2 tier-1 + best 1 paper** | **26/26** | **25/26** |

**Chosen:** the best `AUTHORITY_K = 2` tier-1 chunks (labels, guidelines) plus the best
`SUPPORT_K = 1` lower-tier chunk (paper), filling from the other side if one is short. This keeps the
spec's intent ("prefer authority"), not its exact wording.

**Supporting papers only when close.** Reading the returned papers showed the paper slot was
relevant for gene–drug flags but noise for lab and lifestyle flags (a simvastatin paper for
metformin eGFR; a warfarin frailty table for paracetamol and liver). Every paper judged relevant was
within cosine distance 0.222 of the query and every irrelevant one 0.249 or more, so a paper is used
only within **`SUPPORT_MAX_DISTANCE = 0.235`**; otherwise a third label chunk is returned. Result:
26/26 gold; 14 of 26 cases get a supporting paper, none of them irrelevant.

**Caveat:** the threshold was set on the same 25 cases it was judged on. Recheck it on Person A's
real flags in the evaluation step.

Both the in-memory evaluation and the SQL search use the same `assemble` rule, and
`make verify-store` checks that they return identical top 3s.

---

# The retriever (step 7)

## Use

```python
from drug_safety.rag.retriever import PgRepository, retrieve, build_context

with connect() as conn:
    evidence = {f.flag_id: retrieve(f, PgRepository(conn), embedder) for f in flags}
context = build_context(evidence, max_tokens=..., count=token_counter)  # for the LLM step
```

`retrieve` returns up to 3 `RetrievedChunk`s per flag. Each holds the contract's `EvidenceChunk`
(chunk ID, document, source, tier, title, heading, text, licence), its distance, and `via_class`.

## What it does, per flag

1. **Query** = `flag.query_text()`; **filter** = `flag.drug_ids()` (both drugs for an interaction).
   Both come from the contract, so the engine and the retriever can't disagree.
2. Rank by pgvector cosine distance, separately for **tier 1** (labels, guidelines) and **papers**.
3. **Select**: the best 2 tier-1 chunks plus the best paper, if it is within distance 0.235 **and the
   flag isn't a lab flag** (no lab literature was collected, so a paper there can only be noise).
   If one side is short, fill from the other.
   **CPIC recommendations** (one chunk per drug x phenotype) count only for a **gene flag**, only
   for **the flag's own phenotype** ("Likely poor" = "poor", as CPIC says; any other gene in the
   recommendation must be normal), and **at most one per flag**, so the other tier-1 slot keeps the
   label. That gives the spec's worked-example evidence: "CPIC clopidogrel guideline chunk, label
   pharmacogenomics section". DPWG recommendations (source `dpwg`, for the pairs CPIC doesn't
   cover) follow the same rule.
   **The flag drug's own label first:** in the 2 main tier-1 slots, a passage from the flag
   drug's own label (both drugs for an interaction) comes before another drug's label that only
   mentions it. Found 2026-11-14: a warfarin flag got a Celebrex passage on "CYP2C9 substrates
   such as warfarin" in place of the warfarin label's pharmacogenomics section. A fill-in third
   slot (when there is no paper) still goes by plain distance: own-first there replaced a
   methotrexate passage with an unrelated antiretroviral one. Measured on all 26 gold flags:
   only the warfarin flag changed.
4. **Class fallback**: if no chunk names the drug, search `class_drugs` (e.g. a label passage on
   "NSAIDs" for an ibuprofen flag); those chunks are marked `via_class=True`.
5. **`build_context`** fits all flags' evidence into a token budget, dropping papers first, then the
   farthest label chunk, and never a flag's last chunk.

`PgRepository` (PostgreSQL) and `InMemoryRepository` (tests, offline) implement the same interface
(spec: "functions over a repository interface"). `langchain_retriever(...)` wraps the same rule as a
LangChain `BaseRetriever`.

**One rule, one place:** the evaluation (`eval_retrieval`), the database check (`verify-store`) and
the retriever all use `retriever.select`. After moving the rule there, every result was identical
to before (26/26, SQL = in-memory, the same 14 supporting papers).

## How to know it works

```bash
make eval-flags     # the 26 gold cases as real Flags, through retrieve() and PostgreSQL
```

It must end in `RESULT: PASS`, which needs:
- every flag's expected guideline or label passage in its evidence (spec definition of done). Last
  run: **26/26**. The expected label passage was found for 25/26 flags, and the CPIC
  recommendation for the same drug and phenotype for 8/9 gene flags (warfarin has none). For
  celecoxib the CPIC chunk took the expected label section's slot; another label section is still
  in the evidence;
- CPIC recommendations only for gene flags, only for their own phenotype (checked from the chunk
  text, independently of the retriever's rule);
- no lab flag with a supporting paper;
- the spec's worked example (p18: clopidogrel + CYP2C19, clopidogrel + omeprazole, metformin + eGFR)
  fitting a 1,500-token context **with each flag's expected passage still kept** (last run: 9 chunks,
  2,649 tokens; 5 kept, 1,199 tokens, all expected passages).

It prints the supporting paper for each flag, so a person can read them. Last run: 14 papers, all on topic.

[`tests/gold/retrieval_flags.yaml`](../../../tests/gold/retrieval_flags.yaml) holds the 26 flags,
written with short engine-style reasons rather than hand-tuned queries.

## Found while checking

- **Embeddings can't tell phenotypes apart.** With CPIC loaded and no phenotype rule, 3 of 8 gene
  flags got CPIC advice for the wrong phenotype:
  - a CYP2D6 *ultrarapid* codeine flag got the *poor* metabolizer advice;
  - SLCO1B1 poor function got *increased* function;
  - CYP2C9 poor got *intermediate*.
  The gold check passed anyway, because it looks for the expected *label* passage. Hence the
  phenotype rule above, checked by the eval and by unit tests (5 deliberate breaks, each caught).
- With Flag-built queries, **one irrelevant paper slipped under the 0.235 cut-off** (atorvastatin
  + raised ALT: an OATP pharmacogenetics study at 0.231). Rather than nudging the number again on the same
  cases, lab flags now get no supporting papers, for a stated reason (no lab literature collected).
- Context trimming was checked to keep the *expected* passage, not just "some" passage, at 1,500,
  2,500 and 4,000 tokens.

## Tests

`tests/rag/test_retriever.py`: both drugs filtered for an interaction; labels plus a close paper; a far
paper dropped; no paper for a lab flag; class fallback; nothing found returns nothing; context trimming
order; the LangChain wrapper. **The tests were tested:** three deliberate breaks to `retriever.py`
(papers for lab flags; first drug only; dropping the highest tier first) were each caught by their
test. `tests/rag/test_store_db.py::test_pg_repository_search_and_get` covers the PostgreSQL
repository, including a refused column name (no SQL injection through `column`).
`tests/rag/test_cpic.py`: which CPIC rows are kept, how a chunk reads, tagging with its own drug,
and the phenotype rule (the flag's own phenotype only, "Likely" = confirmed, other genes normal,
gene flags only, one guideline chunk per flag, the label kept). Five deliberate breaks were each
caught.

---

# Environment variables (whole rag module)

| Variable | Default | Used for |
|---|---|---|
| `DATABASE_URL` | `postgresql://drugsafety:drugsafety@localhost:5432/drugsafety` | `kb.documents` and `kb.evidence_chunks` (store, retriever, evaluations) |
| `EMBEDDING_MODEL` | `BAAI/bge-base-en-v1.5` | Embeddings and token counting; must match the stored vectors (changing it means re-embedding) |
| `DATA_DIR` | `data` | Chunk files, the embedding cache (`data/cache/embeddings/`), outputs |
| `LLM_CONTEXT_TOKENS` | `3500` | Evidence budget per patient in `build_context` (used through the llm module) |
| `LOG_LEVEL` | `INFO` | Logging |
