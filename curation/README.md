# curation: rules written from FDA label text

**Owner:** Person B (spec: "Rule curation from labels") · **Status:** 37 rows **drafted, none
approved yet** · Last verify: all checks pass

These rows decide what gets flagged, so nothing here is used until a person approves it.

| File | Contents |
|---|---|
| `lab_drug_rules.yaml` | 10 rules: a lab value crossing a label threshold (eGFR, ANC) |
| `lifestyle_drug.yaml` | 27 rules: smoking, alcohol or a diet tag with a drug |
| `coverage_notes.yaml` | Why each drug without rules has none, per table (spec: "empty cells are reviewed, not ignored") |
| `severity_rules.yaml` | How severity is set: a phrase in the label quote → a severity |
| `units.yaml` | Converting label units to the canonical unit |
| `diet_tags.yaml` | The diet-tag vocabulary (`grapefruit`, `high_vitamin_k`, `caffeine`) for Person A's meal normaliser |
| `../docs/curation_review.md` | **The review sheet**: every rule with its exact label quotes and headings |

## How to review (no code needed)

1. Run `make curation-review` and open `docs/curation_review.md`.
2. For each rule, read the quotes. Ask: does the quote really say this? Is the threshold right?
   Is the severity right? Items marked **⚠ For the reviewer** are judgement calls that need an answer.
3. To approve, edit the YAML row: `status: approved`, `reviewed_by: <name>`,
   `reviewed_on: <YYYY-MM-DD>`, and for flagged rows `review_answer: <your answer>`.
   To reject: `status: rejected` plus a `notes` line saying why.
4. To change a severity the phrase table doesn't give, set `severity_override_reason`.
5. Run `make verify-curation` (must end `RESULT: PASS`), then
   `.venv/bin/python -m drug_safety.curation.rules export` to write the **approved** rows to
   `data/processed/curation/*.csv` for Person A.

The spec also asks Person A to review a 10% sample of these rows.

## What `make verify-curation` checks, for every row

| Check | Catches |
|---|---|
| quote is in its chunk, and the chunk is from **the same drug's** label | invented or misattributed evidence |
| the label number is in a quote, the label unit is in a quote, and the threshold equals that number converted to the canonical unit | wrong thresholds and unit mistakes |
| the words next to the number agree with the operator ("below" for `<`, "above" for `>`) | flipped rules |
| the severity basis phrase is in a quote, and the severity matches the phrase table (or has an override reason) | severities chosen by feel |
| a boxed-warning basis really comes from a boxed-warning section | overstated severity |
| RxCUI matches the verified list; the measure and LOINC are known; lifestyle values exist in the Patient contract; no rule for "never" smokers or "none" alcohol | unusable rules |
| CLcr-based rules carry a `proxy_note`; no proxy note where none is needed | silent substitution of eGFR for CLcr |
| approved rows have a reviewer, a date, and answers to their review flags | unreviewed approvals |
| every drug has rules or a coverage note in each table, never both | forgotten drugs |
| the rules the spec's examples rely on exist (metformin eGFR < 30, smoker on clozapine, warfarin vitamin K, simvastatin grapefruit) | lost key rules |

**The checks were themselves tested** (`tests/curation/test_rules.py`): an edited quote, a quote
from another drug's label, a wrong threshold, a label number missing from the quote, a severity
against its phrase, a basis phrase missing from the quote, a flipped operator, a missing proxy note,
approval without a reviewer, approval with an unanswered flag, a lifestyle value the contract doesn't
allow, a rule for non-users, and a missing coverage note: each makes verify fail.

## How the rows were drafted (2026-11-11)

1. Every label sentence with a lab number (eGFR, CLcr, ANC, platelets, ALT, ULN...) and every
   sentence mentioning smoking, alcohol, grapefruit, vitamin K, caffeine or St. John's wort was
   listed and read.
2. Kept only sentences that tell you **what to do** at a value. Dropped pharmacokinetic facts
   ("plasma clearance was 250 mL/min"), incidence figures ("myopathy in 0.03%"), and false
   matches ("grapefruit juice has no impact on amlodipine", "warfarin alcohols", "adjusted for smoking").
3. Each quote was copied by hand; a script only looked up which chunk contains it, and stopped if a
   quote wasn't found word for word in that drug's label.

## Decisions to know about

- **CLcr vs eGFR:** labels often state creatinine clearance (Cockcroft-Gault, needs body weight). The
  Patient contract has no weight, so these rules use eGFR (CKD-EPI 2021) as a proxy and say so in
  `proxy_note`; FDA's 2020 draft renal guidance allows either. A pharmacist should confirm each.
- **Ranges** like "CLcr 15–29" or "ANC 500–999" are written as "below the next whole number"
  (< 30, < 1000/µL). When several rules on one drug and measure fire (metformin at eGFR 28 fires both
  < 30 and < 45), the rule engine should report the most severe (a note for Person A).
- **Alcohol amounts:** labels say "3 or more drinks a day" or "substantial quantities"; the contract
  has only none/occasional/regular/heavy, so these map to `heavy`, flagged for review.
