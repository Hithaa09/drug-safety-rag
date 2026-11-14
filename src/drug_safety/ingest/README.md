# ingest: Person B loaders

**Owner:** Person B

| Loader | What it loads | Status |
|---|---|---|
| [`dailymed.py`](dailymed.py) | FDA drug labels for the 25 v1 drugs | **Done**, verified 2026-11-11 |
| [`openfda.py`](openfda.py) | Application holder, who made each pinned label, IDs; proposes better labels (not the label text) | **Done**, verified 2026-11-11 |
| [`rxclass.py`](rxclass.py) | Drug classes (NSAID, SSRI, statin...) per v1 drug, from NLM RxClass | **Done**, verified 2026-11-11 |
| [`pubmed_select.py`](pubmed_select.py) | Which research papers to use (PubMed metadata only) | **Done**: 446 selected |
| [`pmc.py`](pmc.py) | Full text of the selected papers (PMC BioC), licence decision | **Done**: 344 full text, 96 metadata only |
| [`rxnorm_names.py`](rxnorm_names.py) | Every prescribable drug name (RxNorm IN + BN), for the LLM validator's drug check | **Done**: 4,970 ingredients, 4,091 brands |
| [`cpic.py`](cpic.py) | CPIC guideline recommendations (tier-1 evidence text) for the v1 drugs and genes | **Done**: 9 guidelines, 184 recommendations (CC0), HLA-B included |
| [`dpwg.py`](dpwg.py) | DPWG guideline recommendations (via ClinPGx) for the pairs CPIC doesn't cover | **Done**: 3 guidelines, 4 recommendations (CC BY-SA 4.0, non-commercial) |
| [`bdpm.py`](bdpm.py) | The French acenocoumarol label (BDPM), with an English translation | **Built**: 13 passages translated; **0 reviewed yet**, so no evidence until a person checks them |

Order: `make ingest-dailymed` → `make fetch-openfda` → `make fetch-classes` → (in rag) `make chunk-labels`.
Each has a `verify-...` command; `make check-updates` tells you when DailyMed has a newer label.

---

# openfda

## What it does

openFDA has the same label *text* as DailyMed (checked: 23 of 24 labels at the same version), so
the text isn't used. It's used for three things DailyMed's files don't give:

1. **Who made the pinned label.** Each label is classified as:
   - `holder_or_manufacturer`: the label's XML says the company manufactures the drug, or its
     name matches the application holder in drugs@FDA;
   - `repackager`: the XML says it only repacks or relabels, or "repack" is in its name;
   - `unclear`: different name and no information either way. In practice these are the same
     company under a later name (Upjohn and Viatris, Barr and Teva, Watson and Actavis); check by eye.
2. **A better label, if there is one.** For a repackager (or unclear) label, it looks for the
   application holder's own oral label under the same application number and **proposes** it.
   It never edits `scope/dailymed_labels.yaml`.
3. **IDs for Person A:** `data/processed/openfda/ids_for_person_a.csv`, with RxCUI, NDC, UNII
   and brand name per drug, to cross-check their RxNorm tables.

**Not used: openFDA's drug-class fields.** They were tested and were wrong for several drugs
(metformin listed as a sulfonylurea; combination-product classes on amlodipine and paracetamol)
and empty for six others. Drug classes come from `rxclass.py` instead.

## Commands

```bash
make fetch-openfda    # network, about 1 minute; prints the labeler table and proposals
make verify-openfda   # offline checks; ends with RESULT: PASS or FAIL
.venv/bin/python -m drug_safety.ingest.openfda report   # print the table again, offline
```

## What verify checks

One row per pinned label, matching the current pins; no fetch errors; only the expected label
missing (paracetamol's OTC label isn't in openFDA); raw responses kept; each label at the same
version as DailyMed; RxCUI present (except two labels where openFDA leaves it empty); each
labeler role matches its evidence; each proposal really is the application holder's own label.

## Accepting a proposed label

1. Run `make fetch-openfda` and read the proposals.
2. In `scope/dailymed_labels.yaml`, change that drug's `setid` and `title`, set `review: ok`,
   and update the `note`.
3. Run `make ingest-dailymed`, `make fetch-openfda`, `make chunk-labels`, then every
   `verify-...` command.

## Limits

- openFDA without a key allows 1,000 requests a day; a full fetch uses about 100. Put a free key
  in `.env` (`OPENFDA_API_KEY`) if you run it often.
- `is_original_packager` is blank on the labels checked, so it isn't used.

---

# rxclass

## What it does

It assigns each v1 drug to the drug classes that label text refers to ("Avoid **NSAIDs**",
"other **SSRIs**"). Membership comes from **NLM RxClass**: the FDA Established Pharmacologic
Class and MED-RT mechanism, matched on the plain ingredient so combination products can't leak
in. [`scope/drug_classes.yaml`](../../../scope/drug_classes.yaml) only says which RxClass classes
make up each class and which words mean it. Acenocoumarol (not in RxClass, because it's not a US
drug) is added by hand, with the reason written next to it.

Result: nsaid = aspirin, ibuprofen, diclofenac, celecoxib · ppi = omeprazole, pantoprazole ·
statin = simvastatin, atorvastatin, rosuvastatin · ssri = sertraline, citalopram · tca =
amitriptyline · opioid = codeine, tramadol · vka = warfarin, acenocoumarol · antiplatelet =
clopidogrel, aspirin · ccb = amlodipine. Seven drugs are in none of these classes (metformin,
phenytoin, clozapine, theophylline, paracetamol, methotrexate, allopurinol).

## Commands

```bash
make fetch-classes    # network, a few seconds
make verify-classes   # 13 checks; every class must have exactly the expected members
```

`verify-classes` holds the expected members of every class (the list above). If RxClass or the
class file ever changes membership, it fails.

**To add a class:** add it to `scope/drug_classes.yaml` (RxClass class name and text phrases),
add its expected members to `EXPECTED` in `rxclass.py`, then run `make fetch-classes`,
`make verify-classes`, `make chunk-labels` and `make verify-chunks`.

Attribution required by NLM is stored in `data/processed/drug_classes.json`.

---

# check-updates (DailyMed)

`make check-updates` asks DailyMed for the newest version of every pinned label and prints
`UPDATE` next to any label with a newer version than the one loaded. It ends with
`RESULT: UP TO DATE` or `RESULT: UPDATES AVAILABLE`. Run it monthly (the spec says to refresh
labels on DailyMed's update files). Last run, 2026-11-11: all 24 up to date.

---

# dailymed

## What it does

For each of the 25 v1 drugs, it:

1. Looks up **which label to use** in [`scope/dailymed_labels.yaml`](../../../scope/dailymed_labels.yaml).
   It never picks a label on its own at load time.
2. **Downloads** that label's XML (the official SPL format) from the DailyMed web service.
3. **Saves the XML untouched** in `data/raw/dailymed/`.
4. **Splits the label** into its sections and subsections (Drug Interactions > 7.2 CYP2C19
   Inhibitors, and so on) and turns each one into clean plain text.
5. **Writes** one JSON file per label, plus a manifest that summarises the run.

It doesn't write to the database yet. The JSON matches the future `kb.documents` row, and the
database load is added once the table schema is agreed with Person A.

## Quick reference

```bash
make ingest-dailymed      # download and split all 25 labels (about 1 minute)
make verify-dailymed      # offline: check everything on disk is complete and correct
make test                 # unit tests (offline)

# One drug at a time
.venv/bin/python -m drug_safety.ingest.dailymed load --drug warfarin
# Propose a label for a drug (does not change anything)
.venv/bin/python -m drug_safety.ingest.dailymed suggest --drug warfarin
```

## How to know it worked (no code review needed)

Run `make verify-dailymed`. It ends with `RESULT: PASS` or `RESULT: FAIL` and lists every
failure. It needs no network and checks, for every drug:

| Check | What it proves |
|---|---|
| every scope drug has a pin | No drug was forgotten in `dailymed_labels.yaml` |
| in manifest / loaded ok | The drug was actually loaded, with no error |
| recorded as missing | Drugs with no US label are reported, not silently skipped |
| manifest matches pin | You didn't change a pin without reloading |
| processed file exists | The JSON output is there |
| ids and tags | `doc_id`, `drugs` and `load_id` follow [docs/conventions.md](../../../docs/conventions.md) |
| raw XML kept | The untouched source file is on disk |
| raw matches processed | The JSON came from that exact XML version |
| up to date with parser | Re-parsing the raw XML today gives identical sections, so nothing was hand-edited and the parser hasn't changed since the load |
| has sections / no empty sections | The label isn't truncated or blank |
| boilerplate dropped | Product tables and package-label panels were removed |
| Rx: interactions + contraindications | Every prescription label has both, including old-format labels that keep them under Precautions |

Last real run: **290/290 checks passed**; 24 labels loaded, plus acenocoumarol reported as missing.

Things `verify` can't decide for you (human judgement):
- whether the chosen label is the **best** one for a drug (the `review: check` list it prints);
- what to do about **acenocoumarol** (no US label).

## Inputs

| File | Purpose |
|---|---|
| `scope/v1.yaml` | The 25 drugs |
| `scope/dailymed_labels.yaml` | One pinned label per drug: `setid`, `title`, `kind` (brand / generic / otc / none), `review` (ok / check / missing), optional `query` and `note` |

## Outputs

| Path | Contents |
|---|---|
| `data/raw/dailymed/<setid>/v<version>.xml` | Untouched XML. A version is never overwritten; a newer version gets a new file |
| `data/processed/dailymed/<setid>.json` | Metadata plus all sections (example below) |
| `data/processed/dailymed/_manifest.json` | One row per drug: status, set ID, version, section count, has interactions / contraindications |

Example (Plavix, shortened):

```json
{
  "doc_id": "dailymed:de8b0b67-eb25-4684-83b5-7ad785314227",
  "source": "dailymed",
  "external_id": "de8b0b67-eb25-4684-83b5-7ad785314227",
  "title": "PLAVIX (CLOPIDOGREL) TABLET, FILM COATED [SANOFI-AVENTIS U.S. LLC]",
  "labeler": "Sanofi-Aventis U.S. LLC",
  "doc_type": "HUMAN PRESCRIPTION DRUG LABEL",
  "source_version": "5",
  "effective_date": "2025-05-30",
  "drugs": ["clopidogrel"],
  "genes": [],
  "study_type": "label",
  "tier": 1,
  "raw_path": "data/raw/dailymed/de8b0b67-eb25-4684-83b5-7ad785314227/v5.xml",
  "load_id": "dailymed-20261111T081529",
  "sections": [
    {
      "section_key": "drug_interactions",
      "section_code": "34073-7",
      "code": "42229-5",
      "heading_path": ["7 DRUG INTERACTIONS", "7.2 CYP2C19 Inhibitors"],
      "text": "Clopidogrel is metabolized to its active metabolite in part by CYP2C19. ..."
    }
  ]
}
```

Section fields:

| Field | Meaning |
|---|---|
| `section_key` | Top-level section in snake_case, e.g. `drug_interactions`, `contraindications`, `clinical_pharmacology` |
| `section_code` | Official LOINC code of the top-level section |
| `code` | LOINC code of this subsection (`42229-5` means "unclassified") |
| `heading_path` | Headings from the top down. **Use this to find subsections** such as "12.5 Pharmacogenomics" or "8.6 Renal Impairment", which have no code of their own |
| `text` | Plain text. Paragraphs on separate lines, list items as `- item`, table rows as `cell \| cell`; table footnotes become `[note N]` markers, listed as `Note N: ...` lines below the table |

`genes` is empty on purpose: chunks are tagged with genes in the `rag` step.

## How labels were chosen, and how to change one

DailyMed lists many copies of each drug's label (109 for clopidogrel, mostly repackagers), so
labels are **chosen once by a person and pinned**, not picked at load time.

The `suggest` command applies this rule, and a person confirms the result:
1. a prescription brand (NDA) label, oral tablet or capsule, single ingredient, updated in the last 3 years;
2. otherwise the newest generic (ANDA) label;
3. otherwise an older brand label;
4. otherwise the OTC "Drug Facts" label.

**To change a drug's label:**
1. Find the label on dailymed.nlm.nih.gov, or run `suggest --drug <name>`. Copy its set ID.
2. Edit that drug's entry in `scope/dailymed_labels.yaml`: `setid`, `title`, `kind`, `review`, `note`.
3. Run `load --drug <name>`, then `make verify-dailymed`.

## Known limitations

| Item | Effect | What to do |
|---|---|---|
| Acenocoumarol | No US label exists, so nothing is loaded | Decide with Person A: drop from v1, or add a non-US source (check its licence) |
| Aspirin, paracetamol | OTC labels: warnings only, no full prescribing sections | Fine for v1; CPIC and papers add depth |
| 6 labels marked `check` (aspirin, diclofenac, metformin, paracetamol, theophylline, tramadol) | Usable; OTC labels, extended-release forms, or a repackager copy whose holder has no label (theophylline) | Review when convenient; reasons are in each pin's `note`. The 3 repackager copies with a holder label were replaced on 2026-11-11 |
| Old-format labels (ibuprofen, theophylline, amitriptyline) | Interactions sit under `PRECAUTIONS` or `WARNINGS`, not a `drug_interactions` section | Downstream code must search `heading_path`, not only `section_key` |
| Images | Figures and chemical structures are dropped | Text and tables are kept |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `verify` says "pin changed since last load" | You edited `dailymed_labels.yaml`. Run `load --drug <name>` |
| `verify` says "parser changed since this was loaded" | The parsing code changed. Run `make ingest-dailymed`; old raw files are reused, nothing is lost |
| `load` reports `asked for X, got Y` | The set ID in the pin is wrong or was retired. Re-pick the label |
| `RuntimeError: DailyMed request failed after retries` | Network problem or DailyMed down. Rerun later; each drug is retried 4 times |
| `not in scope/v1.yaml` | You passed a drug name that's not in the 25. Use lowercase ingredient names |

## Tests

`pytest tests/ingest`: offline, using a small made-up label in `tests/ingest/fixtures/mini_spl.xml`.

| Test | Proves |
|---|---|
| `test_metadata` | Set ID, version, date, document type and labeler are read correctly |
| `test_sections_and_keys` | Sections come out in document order with the right keys and headings |
| `test_unclassified_subsection_found_by_title` | "12.5 Pharmacogenomics" is kept and findable even without a code |
| `test_boilerplate_and_highlights_skipped` | Product tables, package labels and the highlights summary are dropped |
| `test_text_rendering` | Inline formatting, line breaks, lists and tables become clean text; table footnotes move below the table; no text is duplicated |
| `test_untitled_subsections_merge_into_previous` | Untitled blocks ("Bleeding", "Data") stay with their heading |
| `test_untitled_coded_subsection_uses_code_name_as_heading` | OTC headings such as "Do not use" are kept |
| `test_single_ingredient_oral` | Combinations, injections and kits are rejected by `suggest`; a comma in the dosage form ("CAPSULE, DELAYED RELEASE") isn't mistaken for a combination |
| `test_pins_cover_every_scope_drug` | The pin file covers all 25 drugs with valid, unique set IDs |
| `test_run_load_writes_raw_processed_and_manifest` | A load writes the raw file untouched, the JSON and the manifest |
| `test_mismatched_setid_is_an_error` | A wrong set ID is reported, not silently loaded |
| `test_partial_load_keeps_other_manifest_rows` | Loading one drug doesn't wipe the others from the manifest |
| `test_verify_*` | `verify` passes on good output and catches a deleted raw file or a hand-edited JSON |

## Environment variables

| Variable | Default | Use |
|---|---|---|
| `DATA_DIR` | `data` | Where `raw/` and `processed/` go |
| `SCOPE_FILE` | `scope/v1.yaml` | Drug list |
| `LOG_LEVEL` | `INFO` | Use `DEBUG` for every HTTP request |

## Design decisions

- **Pinned labels, not automatic choice.** Automatic choice returned injections, a skin patch and
  a 2013 repackager copy. Pinning makes runs reproducible and keeps the choice a human decision.
- **Raw files are immutable and versioned** (`v<version>.xml`), so any output can be rebuilt and
  traced to the exact label text.
- **Subsections are separate records,** because the parts that matter most (pharmacogenomics,
  renal and hepatic impairment) are unclassified subsections inside larger sections.
- **No database writes yet,** so this step doesn't depend on Person A's schema.
- **Polite to DailyMed:** a 0.25 s pause between requests, 4 retries with backoff, a generic
  User-Agent and no personal details.

---

# pubmed_select

## What it does

Finds candidate research papers through NCBI E-utilities (an approved PMC/PubMed service, at most
3 requests a second), downloads their **PubMed metadata only** (title, abstract, publication type,
MeSH, PMCID, DOI), and decides which to use. Every excluded paper keeps its reason.

**Why these searches** (measured first; docs/progress.md step 6a): strict tiers 1–3 give only about
350 relevant open-access papers; lifestyle literature searches are too imprecise (0–2 relevant in
samples of 12–15), so lifestyle evidence comes from labels instead. The searches used:
- **drug–gene:** each CPIC v1 pair, plus acenocoumarol via DPWG; open access; humans; gene in title/abstract;
- **drug–drug:** a v1 drug as major topic with another v1 drug, MeSH "Drug Interactions"; open access.

## The rules, in order (first match excludes)

| # | Rule | Excluded (2026-11-11) |
|---|---|---|
| 1 | Needs a PMCID (reusable full text) | 0 |
| 2 | Title/abstract names the drug **and** the gene (or two v1 drugs) | 48 |
| 3 | Title names a v1 drug, gene, drug class, or a context word (MTX, antidepressant, antipsychotic, anticoagulant, platelet reactivity) | 146 |
| 4 | Human study (MeSH "Humans") | 9 |
| 5 | Not a comment, letter, editorial or erratum | 3 |
| 6 | No animal study named in the title | 4 |
| 7 | Not a laboratory-method paper (PCR, assay) | 3 |
| 8 | Not a lab or computer study (cell lines, microsomes, in vitro, molecular models) | 29 |
| 9 | Not a probe, cocktail or phenotyping study | 12 |
| 10 | Not "(lack of) effect of <non-v1 drug> on <v1 drug> pharmacokinetics" | 24 |
| 11 | Not a narrative review (not an evidence tier in the spec) | 28 |
| 12 | Tier: 1 guideline, 2 systematic review or meta-analysis, 3 trial or other human clinical study, 4 case report | — |
| 13 | Case reports only for pairs with fewer than 3 tier 1–3 papers | 12 |

**Result: 764 candidates → 446 selected** (tier 1: 1, tier 2: 28, tier 3: 417).

## How good is the selection? (read by hand)

Rules 3, 5–8 and 10 were added after reading samples. Each change was checked on a **fresh**
random sample (a new seed), so the rules weren't fitted to the sample they were built from:

| Version | Relevant | Marginal | Off topic |
|---|---|---|---|
| First rules (600 selected), 50 sampled | ~27 (54%) | ~14 | ~9 (18%) |
| + title rule, 30 of its exclusions read | 15 correctly removed, ~4 relevant wrongly lost → context words added | | |
| + context words (460), fresh 50 | ~37 (74%) | ~6 | ~7 (14%) |
| **Final (446), fresh 50** | **~37 (74%)** | **~8 (16%)** | **~5 (10%)** |

**Honest estimate: about 330 clearly relevant papers, about 400 at least on topic. Below the
spec's 500.** Remaining off-topic papers are things like a formulation bioequivalence study or an
in vitro study without lab MeSH terms. The retriever's drug filter and tier ordering limit their effect.

**Thin pairs:** ibuprofen and celecoxib with CYP2C9 have one selected paper each, and several
drug–drug searches have one.

## Commands and files

```bash
make select-papers             # network (E-utilities), ~2 minutes
make verify-papers-selection   # offline checks; ends with RESULT: PASS or FAIL
```

| Path | Contents |
|---|---|
| `data/raw/pubmed/efetch_*.xml` | Untouched PubMed responses |
| `data/processed/papers/candidates.jsonl` | Every candidate: metadata, tags, tier, `selected`, `exclusion` |
| `data/processed/papers/queries.json` | The exact search strings used |
| `data/processed/papers/selection_summary.json` | Counts by tier and by exclusion reason |

`verify` checks that every paper is selected or has a reason, there are no duplicate PMIDs or
DOIs, every selected paper has a PMCID, re-applying the rules keeps every selected paper, tiers are
1–4, raw responses are kept, and it reports any drug–gene pair left with no paper (none today).

Tests: `tests/ingest/test_pubmed_select.py` (each rule on a made-up paper; PubMed XML parsing).

---

# pmc

## What it does

1. `make fetch-papers` downloads each selected paper's full text from the **PMC BioC API** (one of
   PMC's approved services) at about one request a second, backing off when throttled. It's
   resumable: papers already on disk are skipped. Raw responses: `data/raw/pmc/<PMCID>.json`.
2. `make parse-papers` decides, **per paper, from its own licence**, whether the full text may be stored:

| Licence | Decision | Papers (2026-11-11) |
|---|---|---|
| CC BY, CC BY-SA, CC0 | full text | 228 |
| CC BY-NC, CC BY-NC-SA | full text, **non-commercial only** (marked per paper) | 116 |
| CC BY-NC-ND, CC BY-ND (no derivatives) | **metadata only** (chunking is a derivative) | 80 |
| NO-CC CODE (open access without a Creative Commons licence) | metadata only | 15 |
| Unknown | metadata only | 1 |
| Not yet in BioC (2026 papers) | not fetched | 5 |

   Kept sections: title, abstract, results, discussion, conclusions, tables. Dropped:
   introduction, methods, references (including reference lists filed as tables), figures,
   acknowledgements, funding, conflicts, author contributions.
3. `make verify-papers` re-derives every licence decision and every section from the raw file.

## Found while checking

- **BioC answers HTTP 200 with an error page** ("[Error] : No result can be found") for papers it
  hasn't processed. The fetcher first accepted these because they start with "["; it now requires
  valid BioC JSON, and these 5 papers are reported, not stored.
- **A version-number bug turned "CC0" into "CC"**, which would have wrongly made public-domain papers
  metadata-only. Caught by a test and fixed.
- **A letter to the editor** tagged only "Journal Article" in PubMed had no abstract; selection now
  drops papers without an abstract (`pubmed_select reclassify` re-applies rules offline).

Tests: `tests/ingest/test_pmc.py` (licence policy, kept and dropped sections, error pages).

---

# rxnorm_names

## What it does
Downloads NLM's Prescribable RxNorm ingredient (IN) and brand (BN) names (2 requests), removes the
words in [`scope/drug_name_ignore.yaml`](../../../scope/drug_name_ignore.yaml), and writes
`data/processed/drug_names.json`. The LLM validator uses it to catch Qwen naming a drug that is
neither prescribed nor in a flag (spec rule 4). How the ignore list was made and checked: the
[llm README](../llm/README.md#drug-names-rule-4-where-the-list-comes-from).

## Commands
`make fetch-drug-names` (network), then `make verify-drug-names` → `RESULT: PASS` (9 checks: list
sizes, every US v1 drug present, prasugrel/ticagrelor/Effient/Nexium present, ordinary words and lab
terms absent, every ignore entry really exists in RxNorm, NLM attribution kept).
Raw responses are kept in `data/raw/rxnorm/`.

---

# cpic

## What it does

Fetches CPIC's recommendations from the CPIC database API (`https://api.cpicpgx.org/v1/`, 4
requests) and keeps those that can be evidence for a flag. Each recommendation becomes one chunk
([`rag/chunk_cpic.py`](../rag/chunk_cpic.py)), giving gene flags the spec's "CPIC guideline chunk".

| Kept | Dropped |
|---|---|
| Rows for a v1 drug where a v1 gene has a non-normal phenotype (poor, intermediate, ultrarapid, decreased/increased function), "Likely" phenotypes included | Rows where every v1 gene is normal or indeterminate ("Initiate therapy with recommended starting dose"). The engine only flags abnormal phenotypes, so these could only be wrong evidence. Also rows about genes outside v1 only (HLA-B) |
| One row per distinct text | Rows that repeat the same text for another activity score (CPIC lists amitriptyline 206 times) |

Result 2026-11-11: 458 rows → **171 recommendations, 8 guidelines, 14 drugs**. All 17 CPIC
level-A pairs in v1 are covered except **warfarin** (CYP2C9, VKORC1). CPIC's warfarin guideline is
a dosing algorithm, not a recommendation table, so warfarin keeps its label evidence (and, since
2026-11-14, DPWG's).

**2026-11-14: HLA-B added to v1** (Person A, E3). That gives **184 recommendations, 9
guidelines** (allopurinol is new) and **19 level-A pairs**, all covered except warfarin. HLA-B has
no metabolizer phenotype: CPIC records an allele result ("HLA-B\*58:01 positive") in
`allelestatus`, which is now read for genes with no phenotype.
- "Negative" and "No HLA-B Result" count as no action, like "normal".
- In phenytoin's combined rows a negative HLA-B doesn't stop a CYP2C9 flag from matching.
- How an HLA-B flag states its result is question A10.

Found while checking: both no-action checks used `.match`, which only looks at the start of the
text, so "HLA-B\*58:01 **negative**" counted as actionable. Fixed in both places; a test caught
each.

**Licence:** CC0 1.0, from CPIC's own `LICENSE.md` (github.com/cpicpgx/cpic-data, last changed
2026-03-09). Attribution is requested (CPIC®, www.clinpgx.org, date accessed, version), and the
CPIC logo may not be reused. Every chunk and document carries this. Person A uses the same API for
the gene-drug *rules*; each chunk keeps CPIC's recommendation IDs, so a rule can be linked to its
exact evidence.

## Commands

```bash
make fetch-cpic    # network: raw API responses -> data/raw/cpic/
make chunk-cpic    # parse + one chunk per recommendation -> data/processed/chunks/cpic.jsonl
make verify-cpic   # 17 offline checks
make store-cpic    # embed + load into PostgreSQL (source "cpic")
```

## What verify checks (`make verify-cpic`, must end in `RESULT: PASS`)
- One chunk per kept recommendation; IDs unique.
- Every actionable CPIC row is kept; no chunk is for a normal phenotype.
- Every level-A pair has a chunk, except the listed warfarin gap, which is checked to really be
  empty.
- Each chunk is tagged with one v1 drug and its genes, fits the 480-token limit, and carries the CC0
  licence; each document links its guideline page.
- Spot checks against CPIC's wording:
  - clopidogrel poor metabolizer, cardiac indications: "prasugrel or ticagrelor";
  - the same for neurovascular indications: "an alternative P2Y12 inhibitor";
  - codeine ultrarapid: "Avoid codeine";
  - simvastatin with SLCO1B1 poor function;
  - citalopram poor metabolizer: "50% reduction".

## DPWG: now loaded (see "dpwg" below)
The section below is kept as the record of the decision.
ClinPGx's DPWG annotations would add acenocoumarol (CYP2C9, VKORC1), warfarin (CYP2C9, VKORC1)
and clozapine (CYP2D6), which CPIC doesn't cover with recommendations. Their licence, from
`LICENSE.txt` inside the ClinPGx download (2026-11-05), is **CC BY-SA 4.0 plus**:
- **share-alike:** anyone who receives changed data must agree to the licence first;
- **no sale:** "Under no circumstances can ClinPGx data be sold for other's private or commercial
  use".

Waiting for Person A: question E5 in [docs/questions_for_person_a.md](../../../docs/questions_for_person_a.md).

---

# dpwg

## What it does
Loads the Dutch (DPWG) guideline recommendations from ClinPGx's guideline-annotations download
(1 request), **only for gene-drug pairs CPIC has no recommendation for**. Person A agreed this
on 2026-11-14 (E5), and accepted the licence (E1: the project stays non-commercial). Each
recommendation becomes one chunk ([`rag/chunk_dpwg.py`](../rag/chunk_dpwg.py)), in the same format
as CPIC chunks, so the retriever's phenotype rule applies unchanged.

**What was found when the annotations were read (2026-11-14):**

| Pair (not covered by CPIC) | DPWG says | Loaded |
|---|---|---|
| acenocoumarol + VKORC1 | -1639 AA: 50% of the standard starting dose, more frequent INR checks | 1 chunk |
| warfarin + CYP2C9 | Intermediate: 65%; poor: 20% of the standard starting dose (EU-PACT algorithm) | 2 chunks |
| warfarin + VKORC1 | -1639 AA: 60% of the standard starting dose | 1 chunk |
| acenocoumarol + CYP2C9, clozapine + CYP2D6, citalopram + CYP2D6, sertraline + CYP2D6 | "No action is needed" | none (question C7 for Person A: no flags for these either) |

**Rows that are skipped:**
- "No action" rows (they could only be wrong evidence).
- Exact-genotype rows such as CYP2C9\*1/\*3, which repeat their phenotype row's advice.
- An older 2011 table in the acenocoumarol annotation (superseded in 2018).

**VKORC1:** ClinPGx writes the same genotype as "rs9923231 AA" in a table and as "rs9923231 TT
(-1639 AA)" in its summary, for the same advice. Both are stored as "-1639 AA", with the original
label kept in the chunk. How a VKORC1 flag states its result is question A10.

**Licence:** `LICENSE.txt` inside the download, kept with the raw zip in `data/raw/clinpgx/`. It is
CC BY-SA 4.0, plus ClinPGx's share-alike agreement and "no sale for private or commercial use".
Every chunk carries it.

## Commands
```bash
make fetch-dpwg    # network: data/raw/clinpgx/guidelineAnnotations.json.zip
make chunk-dpwg    # parse + chunks -> data/processed/chunks/dpwg.jsonl
make verify-dpwg   # 14 offline checks, must end in RESULT: PASS
make store-dpwg    # embed + load into PostgreSQL (source "dpwg")
```

## What verify checks
- **Licence:** kept, and it says what we think it says.
- **Coverage:** chunks exist for exactly the 3 pairs above, and none for a pair CPIC covers.
- **Rows:** no "no action" row; one phenotype line per chunk; within the token limit.
- **Licence on every chunk.**
- **Spot checks:** the four recommendations (50%, 60%, 20%, 65%) match DPWG's wording.

---

# bdpm

## What it does
Acenocoumarol has no US label. Person A agreed (E2) to use the French one: SINTROM 4 mg from the
French public medicines database (BDPM, CIS 61510352, ANSM update 20/06/2025) **with a reviewed
English translation**.

| File | Role |
|---|---|
| [`curation/translations/sintrom_bdpm_61510352.yaml`](../../../curation/translations/sintrom_bdpm_61510352.yaml) | The 13 passages a v1 flag can need: French word for word, English translation, what was omitted, review status |
| [`docs/translation_review_sintrom.md`](../../../docs/translation_review_sintrom.md) | The same side by side, for the reviewer (`make bdpm-sheet`) |
| `ingest/bdpm.py` | Fetches the page (1 request) and checks the French against it |
| [`rag/chunk_bdpm.py`](../rag/chunk_bdpm.py) | One chunk per **reviewed** passage, written like a DailyMed label chunk |

**The 13 passages:**
- contraindications (4.3);
- "generally not recommended" (severe kidney impairment, aspirin, NSAIDs);
- kidney and liver impairment;
- dietary vitamin K;
- bleeding risk factors;
- older patients (4.2);
- interactions with the v1 drugs: aspirin, NSAIDs, allopurinol, phenytoin, statins, paracetamol,
  antiplatelets, SSRIs, amitriptyline, tramadol;
- CYP2C9 metabolism (5.2);
- the patient leaflet's lines on vitamin K and alcohol.

Items about no v1 drug are left out, marked "[…]", and each passage's `omitted` says what was
left out.

**Translation:** done by Claude (AI) on 2026-11-14. **A passage becomes evidence only after a
person who reads French sets `reviewed: true`, with their name and the date.** Until then
acenocoumarol's evidence is the DPWG VKORC1 recommendation only.

**Corrections to the earlier source notes, found while doing this:**
- The licence is **Licence Ouverte 2.0**, not 1.0. The page links Etalab's `LO.md`, and 2.0
  explicitly allows adaptation, including translation.
- The label was **updated 20/06/2025**, not June 2018. That date belongs to another section of the
  page (the HAS opinion).
- **Alcohol** appears only in the patient leaflet ("consume in moderation"), not in the prescriber
  sections.

## Commands
```bash
make fetch-bdpm    # network: data/raw/bdpm/61510352.html
make bdpm-sheet    # regenerate the review sheet after editing the YAML
make chunk-bdpm    # chunks from reviewed passages only
make verify-bdpm   # 10 checks, must end in RESULT: PASS
make store-bdpm    # load the reviewed passages (an empty load is fine)
```

## What verify checks
- **The page:** stored, with the update date the translations cite.
- **The French:** every line is on the page **word for word** (a test shows that one changed word
  is caught).
- **The English:** a line for every French line, and of similar length.
- **Omissions:** every "[…]" is explained.
- **Reviews:** a reviewed passage names its reviewer and date, and chunks exist for exactly the
  reviewed passages.
- **Limits:** each passage fits the token limit.
- **The sheet** is up to date.
- It reports how many passages are reviewed.

## How to review (someone who reads French)
1. Open `docs/translation_review_sintrom.md`. For each passage, check that the English says what the
   French says, nothing added or lost.
2. In the YAML, correct `english` if needed, then set `reviewed: true`, `reviewed_by` and
   `reviewed_on`.
3. Run `make bdpm-sheet chunk-bdpm verify-bdpm store-bdpm`.

