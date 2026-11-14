# Questions for Person A

Every open question for Person A, in one place. Each has Person B's proposal, so "agree" is a
valid answer. Write the answer under each one; once a question is settled, Person B updates
the code and the linked file and moves it to **Answered** at the bottom.

**Quickest path:** the questions marked ⭐ block other work. Answer those first.

Status key: ⬜ open · ✅ answered

---

## A. Contracts (`Patient`, `Flag`, `Report`, API response)

Draft: [`src/drug_safety/contracts/`](../src/drug_safety/contracts/README.md), version
`0.1.0-draft`. Answered 2026-11-14 (A7 still open): becomes `0.1.0` once the agreed changes are in.

| # | Question | B's proposal | Status |
|---|---|---|---|
| A1 ⭐ | Are **gene and phenotype required** on gene-drug flags? | Yes: the retriever builds its query from them. The spec's p14 example then needs those two fields added | ✅ |
| A2 ⭐ | Do flags name drugs as **ingredient names or RxCUIs**? | Lowercase ingredient names (`clopidogrel`) with an optional `rxcui`. If the engine uses RxCUIs, the evidence chunks switch too (a reload, not a re-embedding). Also affects B3 | ✅ |
| A3 | Do the **lifestyle values** match your normaliser? | Smoking `never/former/current`; alcohol `none/occasional/regular/heavy`; diet `veg/non_veg` plus free-text meals | ✅ |
| A4 | Beyond age and sex (eGFR, CKD-EPI 2021), does the normaliser need any **other patient fields**? | None (CKD-EPI 2021 doesn't use race). See A8 for weight | ✅ |
| A5 | Which **`flag_id` prefixes** will the engine use? | `gene-`, `ddi-`, `lab-`, `life-` followed by 3 digits (`gene-001`) | ✅ |
| A6 | Add **new vs ongoing** to prescriptions? | Add `Prescription.status: new / ongoing` (optional). Clozapine's label has different ANC rules for starting it and for patients already on it | ✅ |
| A7 | Should alcohol carry an **amount**? | Keep the four levels and agree that `heavy` = 3 or more drinks a day (the labels' wording). Or add an optional `drinks_per_day` | ⬜ |
| A8 | Add optional **body weight**? | Optional. Many labels give creatinine clearance (Cockcroft-Gault, needs weight). Without it the rules use eGFR as a stand-in and say so | ✅ |
| A9 | Record **benign ethnic neutropenia (BEN)**? | Optional `Patient.conditions`. Clozapine's ANC thresholds are lower for BEN | ✅ |
| A10 ⭐ | **(New)** HLA-B (E3) and VKORC1 have no metabolizer phenotype. What goes in their flags' `phenotype`? | The result as the guideline writes it. **HLA-B**: `"HLA-B*58:01 positive"` (allopurinol), `"HLA-B*15:02 positive"` (phenytoin), as CPIC does. **VKORC1**: the -1639 genotype, `"-1639 AA"` (DPWG: 50% of the acenocoumarol and 60% of the warfarin starting dose; "-1639 AG" needs no action). The DPWG chunks already use `"-1639 AA"`; the retriever matches a flag only when the value is identical. **Patient input:** the screen asks for HLA-B as `*58:01 positive` in the diplotype column; your normaliser turns it into the flag's phenotype | ⬜ |

**Answers:**
- A1: Agree with B's proposal (2026-11-14)
- A2: Agree with B's proposal (2026-11-14)
- A3: Agree with B's proposal (2026-11-14)
- A4: Agree with B's proposal (2026-11-14)
- A5: Agree with B's proposal (2026-11-14)
- A6: Agree with B's proposal (2026-11-14)
- A7: Disagree, **no alternative given** — still open: which option? (keep the 4 levels with heavy = 3+ drinks a day, or add `drinks_per_day`, or something else)
- A8: Agree with B's proposal (2026-11-14)
- A9: Agree with B's proposal (2026-11-14)
- A10:

---

## B. Database (Person A owns the warehouse)

SQL proposal: [`warehouse_proposal/`](../warehouse_proposal/README.md).

| # | Question | B's proposal | Status |
|---|---|---|---|
| B1 ⭐ | Copy the **document-store tables** (`kb.documents`, `kb.evidence_chunks`) into your migrations? | Yes, as `0001` and `0002`. Spec p10, plus columns B's modules need: `labeler`, `headings`, `drug_classes`, `class_drugs`, `token_count`, `chunker_version`, `load_id`, `loaded_at`, and the paper ID columns | ✅ |
| B2 | Agree the **ID formats**? | `doc_id` = `{source}:{external_id}`; `chunk_id` = `{doc_id}:{section}:{n:03d}`; `load_id` = `{source}-{timestamp}`. See [docs/conventions.md](conventions.md) | ✅ |
| B3 | Chunk `drugs[]` as **names or RxCUIs**? | Follows A2 | ✅ |
| B4 | When will the **`drug_alias` table** exist? | Until then B uses a temporary file, `scope/drug_aliases.yaml`, for tagging chunks and for the validator. B switches to the table when it exists | ✅ |
| B5 | Check the **RxCUIs and IDs** against your RxNorm tables? | B checked them on RxNav: `scope/rxcui.yaml`, plus `data/processed/openfda/ids_for_person_a.csv` (RxCUI, NDC, UNII and brand per drug) | ✅ |
| B6 | Does **`scope/v1.yaml`** (25 drugs, 5 genes) match your copy exactly? | It must; any difference breaks joins | ✅ |
| B7 | **(New, from E3)** Add **HLA-B** to your copy of `scope/v1.yaml` (B6: the two must match) | Done on B's side on 2026-11-14 (6 genes). CPIC's HLA-B recommendations (allopurinol, phenytoin) are loaded | ⬜ |

**Answers:**
- B1: Agree with B's proposal (2026-11-14)
- B2: Agree with B's proposal (2026-11-14)
- B3: Agree with B's proposal (2026-11-14)
- B4: Agree with B's proposal (2026-11-14)
- B5: Agree with B's proposal (2026-11-14)
- B6:
- B7: Agree with B's proposal (2026-11-14)

---

## C. Rule engine and curated rules

Curated rows: [`curation/`](../curation/README.md); review sheet: [docs/curation_review.md](curation_review.md).

| # | Question | B's proposal | Status |
|---|---|---|---|
| C1 ⭐ | Will you **review a 10% sample** of B's 37 curated rules (spec cross-check)? | Yes. 12 rows carry ⚠ questions; start with those | ✅ |
| C2 | When **several rules on one drug and measure** fire (metformin at eGFR 28 fires both < 30 and < 45), does the engine report only the most severe? | Yes, only the most severe | ✅ |
| C3 | Is the **diet-tag vocabulary** right for your meal normaliser? | `grapefruit`, `high_vitamin_k`, `caffeine` ([curation/diet_tags.yaml](../curation/diet_tags.yaml)) | ✅ |
| C4 | Are **ranges written as "below the next whole number"** acceptable? | "CLcr 15–29" becomes < 30 and "ANC 500–999" becomes < 1000/µL | ✅ |
| C5 | Which **data gaps** does the engine report, and with what wording? | The UI shows any `DataGap`. The stand-in API guesses (no creatinine, no genotype, no smoking or alcohol); your engine decides the real ones | ✅ |
| C6 | Can B review **your gold cases** against the CPIC and label examples (spec cross-check)? | Yes, once they exist | ✅ |
| C7 | **(New, from DPWG)** Should the engine leave out flags for pairs where the guidelines say "no action"? | Yes. DPWG says "no action" for acenocoumarol + CYP2C9 and clozapine + CYP2D6, and CPIC has no recommendation for them, so a flag would have no guideline behind it. B loads no evidence for them | ⬜ |

**Answers:**
- C1: Agree with B's proposal (2026-11-14)
- C2: Agree with B's proposal (2026-11-14)
- C3: Agree with B's proposal (2026-11-14)
- C4: Agree with B's proposal (2026-11-14)
- C5: Agree with B's proposal (2026-11-14)
- C6:
- C7: Agree with B's proposal (2026-11-14)

---

## D. API and interface

Docs: [`src/drug_safety/llm/README.md`](../src/drug_safety/llm/README.md) ("Use from the API") and
[`src/drug_safety/ui/README.md`](../src/drug_safety/ui/README.md) ("For Person A").

| # | Question | B's proposal | Status |
|---|---|---|---|
| D1 ⭐ | Will `POST /v1/assess` call **`llm.explain_patient(...)`** for the evidence and the report? | Yes. It does retrieve → trim → explain and returns what goes into `AssessResponse` (`evidence`, `report`, `report_source`, `notice`). Load the finder and the chat model once at start-up | ✅ |
| D2 | What does the **`GET /health` body** look like? | `{"status": "ok", "checks": {"database": {"ok": true, "detail": "..."}, "model": {...}, "embedding": {...}}}`. The UI shows any body, but this shape displays best. `llm.client.ping(settings)` gives the model check | ✅ |
| D3 | Do errors use **FastAPI's usual `{"detail": ...}`**? | Yes, including the 422 field-error list; the UI turns it into readable messages | ✅ |
| D4 | What is the **API's timeout**? | At least 420 s (two model calls of up to 180 s, plus retrieval), matching the UI's `API_TIMEOUT_S` | ✅ |
| D5 | Use the **stand-in API** as the reference for behaviour? | Yes: `ui/stub_api.py` has the same endpoints and contract, and `make verify-ui` runs the screen against it. Delete it once yours runs | ✅ |
| D6 | Paste the **`ui` service** into `docker-compose.yml`? | Yes: `docker/compose.ui.yml`. It assumes your API service is named `api`, listens on 8080 and has a health check; tell me if not | ✅ |

**Answers:**
- D1: Agree with B's proposal (2026-11-14)
- D2: Agree with B's proposal (2026-11-14)
- D3: Agree with B's proposal (2026-11-14)
- D4: Agree with B's proposal (2026-11-14)
- D5: Agree with B's proposal (2026-11-14)
- D6: Agree with B's proposal (2026-11-14)

---

## E. Sources and licences

Details: [docs/sources.md](sources.md).

| # | Question | B's proposal | Status |
|---|---|---|---|
| E1 ⭐ | Is the project **commercial**, now or later? | If yes, DDInter (CC BY-NC-SA) must be replaced and the 116 non-commercial papers (CC BY-NC, marked per paper) removed. If no, both can stay | ✅ |
| E2 ⭐ | **Acenocoumarol** (no US label): use the French BDPM Sintrom label? | Yes. Licence Ouverte 1.0: credit the source and date, and don't alter it. Use it with a reviewed English translation; gene-drug comes from DPWG via ClinPGx. B builds the loader once you agree | ✅ |
| E3 | Add **HLA-B** to the v1 genes? | Worth it: CPIC level A for allopurinol and phenytoin (severe skin reactions). Not possible with the current 5 genes | ✅ |
| E4 | Are the **6 labels marked `check`** acceptable? | Yes. They aren't repackager problems, just formulation choices (OTC, extended-release) or drugs with no holder label. See `scope/dailymed_labels.yaml` | ✅ |
| E5 ⭐ | Load **DPWG guideline annotations from ClinPGx**? They are the only gene guidance for acenocoumarol, and fill CPIC's gaps for warfarin and clozapine | Yes, only for those 5 pairs (acenocoumarol and warfarin × CYP2C9/VKORC1, clozapine × CYP2D6). Licence: CC BY-SA 4.0 plus share-alike agreement and "no sale for private or commercial use" (ClinPGx `LICENSE.txt`). Fine for an academic project; it ties in with E1 | ✅ |
| E6 | CPIC content is **CC0**, but CPIC's licence also says its API is "bound by" the ClinPGx data usage policy. Treat CPIC chunks as CC0? | Yes: CPIC's own licence says all CPIC curated content is CC0; we credit CPIC on every chunk. If in doubt, they would fall under E5's terms | ✅ |
| E7 ⭐ | **(New, from E2)** Who reviews the **English translation** of the French acenocoumarol label? It needs someone who reads French | B or A, or anyone on the team who reads French. 13 short passages, side by side in `docs/translation_review_sintrom.md`. Until reviewed, acenocoumarol's only evidence is DPWG's VKORC1 recommendation | ⬜ |

**Answers:**
- E1: Disagree with the framing; comment: "let both of them stay". DDInter and the 116 CC BY-NC papers stay. **Consequence: the project must stay non-commercial** (DDInter CC BY-NC-SA, CC BY-NC papers and ClinPGx's "no sale" clause all forbid commercial use). If that ever changes, these three must be replaced first
- E2: Agree with B's proposal (2026-11-14)
- E3: Agree with B's proposal (2026-11-14)
- E4: Agree with B's proposal (2026-11-14)
- E5: Agree with B's proposal (2026-11-14)
- E6:
- E7: Agree with B's proposal (2026-11-14)

---

## F. Papers and retrieval

| # | Question | B's proposal | Status |
|---|---|---|---|
| F1 ⭐ | Accept **fewer than 500 papers**? (Spec definition of done: at least 500.) | 445 selected (about 330 clearly relevant), 344 chunked (those whose licence allows full text). Accept, or widen the search scope | ✅ |
| F2 | May the **80 no-derivatives papers** (CC BY-NC-ND) be chunked? | No: keep them as metadata only, because chunking is arguably a derivative. Including them would give 424 chunked papers | ✅ |
| F3 | Accept the **retrieval rule** that differs from the spec's wording? | Yes: the best 2 labels plus 1 close paper scored 26/26 on the gold flags; the spec's "closest 10" scored 20/26 once papers were loaded | ✅ |
| F4 | Send **real flags** as soon as the engine produces them? | Yes. The paper cut-off (0.235) and the "no papers for lab flags" rule were set on B's 26 hand-written flags and must be rechecked on real ones | ✅ |
| F5 | Gene flags now get **1 CPIC recommendation + 1 label passage + 1 paper**, the CPIC one only for the flag's own phenotype. Agree? | Yes. It matches the spec's worked example ("CPIC guideline chunk, label pharmacogenomics section"); gold 26/26 | ✅ |
| F6 | Link your **gene-drug rules to CPIC recommendation IDs**? Each CPIC chunk keeps them | Optional. If a rule row stores the CPIC recommendation ID, a flag can cite the exact CPIC passage | ✅ |

**Answers:**
- F1: Agree with B's proposal (2026-11-14)
- F2: Agree with B's proposal (2026-11-14)
- F3: Agree with B's proposal (2026-11-14)
- F4: Agree with B's proposal (2026-11-14)
- F5: Agree with B's proposal (2026-11-14)
- F6: Agree with B's proposal (2026-11-14)

---

## G. LLM layer

| # | Question | B's proposal | Status |
|---|---|---|---|
| G1 | Keep **validator rule 7** (no number that isn't in the flags, evidence or patient summary)? B added it; the spec's 6 checks don't include it, though the prompt rules forbid invented numbers | Keep. In the first Colab run it fired on 0 of 15 answers, so it costs nothing | ✅ |
| G2 | Keep **rule 4 strict**? A drug named only in the evidence (e.g. prasugrel in the CPIC text) is rejected | Keep, as the spec says. In the first Colab run it fired on 0 of 15 answers, but that was before CPIC evidence: 6 of 8 CPIC chunks for the test patients name alternatives. The prompt now tells Qwen to write "an alternative antiplatelet"; the next run will show the cost | ✅ |
| G3 | **Only synthetic patients on Colab**? | Yes: Colab is Google's machine. Real patients wait for a project GPU server | ✅ |
| G4 | Where will the **GPU server** run after Colab? | Any machine that can run `vllm serve`. Only `MODEL_URL`, `MODEL_NAME` and `MODEL_API_KEY` change | ✅ |
| G5 | Suggestions may start "Consider **monitoring/checking/assessing/evaluating/discussing**", not only the spec's "Consider reviewing". OK? | Yes. Qwen wrote "Consider monitoring the liver function tests" in the first run; rejecting it would cause fallbacks for harmless wording. Switching, stopping and dose changes are still rejected | ✅ |

**Answers:**
- G1: Agree with B's proposal (2026-11-14)
- G2: Agree with B's proposal (2026-11-14)
- G3: Agree with B's proposal (2026-11-14)
- G4: Agree with B's proposal (2026-11-14)
- G5: Agree with B's proposal (2026-11-14)

---

## Answered

**2026-11-14: Person A answered all questions** (`docs/person_a_responses_2026-11-14.json`). 43 agreed with B's
proposal; E1 was answered with a decision (below); **A7 is still open**.

What each answer means for the work:

| Answer | Follow-up | Who |
|---|---|---|
| A1, A2, A3, A4, A5, B2, B3 agreed | Contract as drafted; no change | — |
| A6, A8, A9 agreed | Add optional `Prescription.status` (new/ongoing), `Patient.weight_kg`, `Patient.conditions` (BEN) to the contract and the screen; contract becomes `0.1.0` | B |
| A7 open | Ask Person A which option | B asks |
| B1, D6 agreed | Copy `warehouse_proposal/` into the migrations; paste `docker/compose.ui.yml` | A |
| C1 agreed | Review a 10% sample of the 37 curated rules | A |
| C2 to C6, D1 to D5 agreed | Build the engine and API as proposed | A |
| E1: keep DDInter and the CC BY-NC papers | Project stays non-commercial; recorded in docs/sources.md | B records |
| E2, E5 agreed | Build the BDPM acenocoumarol loader and the DPWG loader (5 pairs) | B |
| E3 agreed: add HLA-B to v1 | Add HLA-B to `scope/v1.yaml` and the aliases; load CPIC's HLA-B recommendations (allopurinol, phenytoin); agree how a HLA-B flag states its "phenotype" (a new question) | A + B |
| E4, E6, F2, F3, F5 agreed | No change | — |
| F1 agreed (344 papers) | Definition of done item 2 becomes an accepted deviation | B records |
| F4, F6 agreed | Send real flags; store CPIC recommendation IDs on rule rows | A |
| G1 to G5 agreed | No change | — |
