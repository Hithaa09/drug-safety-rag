# contracts

**Owner:** Person A (spec), drafted by Person B · **Status:** **`0.1.0`, agreed with Person A on
2026-11-14** (questions A1-A9 in [docs/questions_for_person_a.md](../../../docs/questions_for_person_a.md);
A7, the alcohol amount, and A10, the HLA-B phenotype, are still open)

The shared data shapes every module exchanges (spec: "agree the contracts first, because A's
flags are B's input"). Pydantic models in [`models.py`](models.py); generated JSON schemas in
[`schemas/`](schemas/) for review and for non-Python tools. **Depends on pydantic only** (tested).

| Model | Produced by | Used by |
|---|---|---|
| `Patient` | the UI or an API caller | normalisation, rule engine |
| `Flag` | rule engine (A) | retriever, LLM, validator, UI (B) |
| `DataGap` | rule engine (A) | report uncertainties, UI |
| `Report` | Qwen, checked by the validator (B) | API, UI |
| `EvidenceChunk` | retriever (B) | LLM prompt, UI |
| `AssessRequest` / `AssessResponse` | API `POST /v1/assess` (A) | UI (B) |

## Checked against the spec

The spec's example JSON is kept **verbatim** in
[`tests/contracts/spec_examples/`](../../../tests/contracts/spec_examples/):

| Spec example | Result |
|---|---|
| Report, p16 | Valid as is |
| Report, worked example p19 | Valid as is |
| Patient, worked example p18 (Plavix, omeprazole, metformin, creatinine 2.45, CYP2C19 \*2/\*2, smoker, vegetarian) | Valid |
| The worked example's three flags (p18) and a full response | Valid |
| **Flag, p14** | **Rejected as written**: gene-drug flags need `gene` and `phenotype` (agreed with Person A, A1). Valid once those two fields are added; the test shows both |

## Added in 0.1.0 (agreed 2026-11-14), all optional

| Field | Why | Question |
|---|---|---|
| `Prescription.status`: `new` or `ongoing` | Some labels differ for starting a drug and continuing it (clozapine's ANC rules) | A6 |
| `Patient.weight_kg` (0 to 400) | Lets renal rules use creatinine clearance (Cockcroft-Gault), as many labels do; without it they use eGFR and say so | A8 |
| `Patient.conditions`: `benign_ethnic_neutropenia` | Clozapine's ANC thresholds are lower for BEN. Only conditions that change a rule are allowed | A9 |

Older patient files without these fields still load (tested). The screen has inputs for all three,
and Qwen's patient summary mentions them when they're given.

## What the draft adds to the spec, and why

| Addition | Why |
|---|---|
| `Flag.interacting_drug` (required for `drug_drug`) | The spec's example has one `drug`, but an interaction has two, and the retriever must filter on **either** drug (spec RAG step 2) |
| `Flag.gene` + `phenotype` (required for `gene_drug`) | The retriever builds its query from drug, gene and reason (spec RAG step 1), and the UI shows the genotype |
| `Flag.lab` (required for `lab_drug`): measure, value, unit, operator, threshold | The explanation can say "eGFR 28 is below 30" without inventing numbers; the validator can check numbers against it |
| `Flag.lifestyle` (required for `lifestyle_drug`) | Same reason, for smoking, alcohol and diet tags |
| `Flag.drug_ids()`, `Flag.query_text()` | The two methods the spec's retrieval sketch calls |
| `rule_id` must start with the flag type; `flag_id` looks like `gene-001` | Catches a mislabelled flag early |
| `DataGap` | The spec's "missing data never creates a flag, it creates a note" |
| `AssessResponse`: `report_source` (`llm`, `template`, `none`), `notice`, `disclaimer`, `contracts_version` | The spec's fallback ("template summary and a notice") and fixed banner, returned explicitly; flags must be sorted by severity, and `evidence` keys must be real flag IDs |
| **Unknown fields are rejected everywhere** | A typo in a patient file, or a field Qwen invents (`"dose_advice"`), fails loudly instead of being ignored (spec validator rule 1) |
| `Suggestion.evidence_ids` needs at least one ID | Spec validator rule 5 |
| `FlagExplanation.evidence_ids` may be empty | Only when the flag had no evidence, which must then appear in `uncertainties` (spec prompt rules); the validator checks that pairing |

## Questions for Person A

Also listed, with answer space, as A1–A9 in
[docs/questions_for_person_a.md](../../../docs/questions_for_person_a.md).

1. **Gene and phenotype on gene-drug flags:** OK to make them required? If so, the spec's p14
   example needs those two fields added.
2. **Drugs as names or RxCUIs?** The draft uses lowercase ingredient names (`clopidogrel`),
   matching the evidence chunks, with an optional `rxcui`. If the rule engine works in RxCUIs,
   say so: the chunks can switch (a reload, not a re-embedding).
3. **Lifestyle values:** smoking `never|former|current`; alcohol `none|occasional|regular|heavy`;
   diet type `veg|non_veg` plus free-text meals. Do these match what normalisation will produce?
4. **Patient needs age and sex** (for eGFR by CKD-EPI 2021). Anything else your normaliser needs,
   such as weight or race? (CKD-EPI 2021 doesn't use race.)
5. **`flag_id` format** `<prefix>-NNN`: which prefixes will the engine use? (This draft's
   examples use `gene`, `ddi`, `lab`, `life`.)

### Raised by rule curation (step 5)

6. **New or ongoing prescription?** Clozapine's label has different ANC rules for starting it
   and for patients already on it. Add `Prescription.status: new | ongoing`?
7. **Alcohol amount:** labels use "3 or more drinks a day". Add `Lifestyle.drinks_per_day`
   (optional), or keep `none|occasional|regular|heavy` and agree that heavy means 3 or more a day?
8. **Body weight** (optional): many labels state creatinine clearance (Cockcroft-Gault), which needs
   weight. Without it, the rules use eGFR as a proxy.
9. **Benign ethnic neutropenia (BEN):** clozapine's ANC thresholds are lower for patients with
   BEN. Record it (for example `Patient.conditions`)?

## Commands

```bash
.venv/bin/python -m drug_safety.contracts.schemas write   # after changing models.py
.venv/bin/python -m drug_safety.contracts.schemas check   # also run by the tests
pytest tests/contracts
```

## Tests

[`tests/contracts/test_contracts.py`](../../../tests/contracts/test_contracts.py) covers: the spec's
examples above; bad severity, type, rule ID and flag ID rejected; each flag type needs its
trigger; Report rejects invented fields and uncited suggestions; response ordering and
consistency; bad patient input rejected; severity order; JSON schemas up to date; pydantic as
the only dependency.

**The tests were themselves tested:** three deliberate breaks were made to `models.py` (allow
unknown fields; drop the per-type trigger check; let suggestions have no evidence). Each made
the matching tests fail, and the original file was restored unchanged.

## Environment variables
None. The contracts are plain pydantic models and read no settings.
