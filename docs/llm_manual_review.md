# LLM manual review (spec: "a manual check of about 10 reports for invented claims")

One section per Colab run. Each report was read sentence by sentence against its flags and the
evidence passages it was given. "Invented" means a claim that is not in the flags, the patient
summary or the evidence.

---

## Run 1: 2026-11-11, prompt `explain-v1`, plain mode

Qwen 2.5 7B Instruct, vLLM 0.33.0, NVIDIA L4. Files: `data/outputs/2026-11-11_llm_eval/plain/`.
**Read by:** Claude (assistant to Person B). **Confirmed by a person:** ☐ (pending)

The second column gives the verdict of the validator that ran (explain-v1); the third gives the
verdict of the current validator on the same answers (`tests/llm/test_real_answers.py`).

| Patient | v1 verdict | Current validator | Invented claims | Other findings |
|---|---|---|---|---|
| p01 worked example | accepted after retry | rejected (both answers) | None | Suggestion "Consider alternative antiplatelet therapy…" is attached to the omeprazole flag. The label's advice for that interaction concerns the PPI, not the antiplatelet, so the advice is misattributed (now rejected, rule 6). Cites a pantoprazole paper for an omeprazole claim (weak support, not wrong) |
| p02 warfarin | template | 2nd answer accepted | None | "Regular monitoring of INR is important to adjust the warfarin dose" leans towards a dose instruction; not caught by rule 6. Gene flag's evidence includes a Celebrex label passage (it mentions warfarin); cited, not misused |
| p03 codeine, paracetamol | accepted after retry | same | None | Faithful: morphine toxicity, ALT 150, heavy alcohol all from the flags and labels |
| p04 simvastatin | accepted | same | None | Faithful |
| p05 methotrexate | template | template | None | 2nd answer copied label instructions ("consider reducing the dosage or discontinuing"), rejected. Calls eGFR 55 "mild renal impairment": "mild" is not in the evidence (minor) |
| p06 clozapine | template | 2nd answer accepted | None | "Below the threshold for clozapine-induced neutropenia" is awkward wording, not wrong |
| p07 atorvastatin | accepted | same | None | ALT "may be related to her atorvastatin use": hedged, matches the label |
| p08 clopidogrel, SSRI | accepted | same | None | "SSRIs ... interfere with serotonin reuptake inhibition" is garbled wording; the bleeding claim itself is right |
| p09 tramadol, theophylline | accepted | rejected | None | "Dose adjustments are necessary", "alternative treatments": instructions, now rejected (rule 6) |
| p10 CYP2C9, CYP2C19 | accepted | same | None | Faithful |

**Result:** no invented drug, number, interaction or finding in any of the 15 answers. Three of
the problems were instructions or misattributed advice; the current validator rejects them. Two
are not caught by any rule (p02 "to adjust the warfarin dose", p05 "mild"): this is why the spec
asks for a human check.

---

## Run 2: 2026-11-14, prompt `explain-v2`, guided JSON and plain, with CPIC/DPWG evidence

Qwen 2.5 7B Instruct, vLLM 0.33.0, NVIDIA L4 (same as run 1). Same 10 synthetic patients.
Files: `data/outputs/2026-11-14_llm_eval/{guided,plain}/`.
**Read by:** Claude (assistant to Person B). **Confirmed by a person:** ☐ (pending)

### Numbers

| | Guided | Plain |
|---|---|---|
| Accepted first try (run's validator) | 3/10 | 3/10 |
| Accepted after retry | 7/10 | 4/10 |
| Template fallback | 0/10 | 3/10 (p06, p09, p10) |
| Not valid JSON | 0 | 1 (p10 retry: missing comma); 1 answer wrapped in a markdown fence |
| Seconds per call (mean / max) | 27.2 / 32.0 | 27.4 / 32.5 |
| First-try issues | 10, all rule 6 | 11, all rule 6 |

Re-judged by the current validator (`tests/llm/test_real_answers_run2.py`), which closes four
rule-6 gaps found in this review (below):

| | Guided | Plain |
|---|---|---|
| Accepted first try | 2/10 (p04, p08) | 2/10 (p04, p08) |
| Accepted after retry | 5/10 | 3/10 |
| Template fallback | 2/10 (p09, p10) | 4/10 (p01, p06, p09, p10) |
| Rejected, no further attempt recorded | p02 (would have been retried) | p02 |

**Decision:** guided JSON is the default from now on (`MODEL_GUIDED_JSON=true`). Text and speed
are the same as plain, but it never produced broken JSON and fell back half as often.

### Guided answers, read sentence by sentence (the accepted answer, or the last one)

| Patient | Run verdict | Current validator | Wrong or unsupported statements | Other findings |
|---|---|---|---|---|
| p01 worked example | after retry | same | None | Faithful. Gene-flag suggestion also mentions the omeprazole (a patient fact) |
| p02 warfarin | 1st try | rejected: "requires a lower dose of warfarin" | "leading to an increased risk of **thrombosis**": not in any passage (the label says vitamin K "may affect therapy"). Summary links the vitamin K diet to bleeding risk: wrong direction (vitamin K lowers warfarin's effect) | INR monitoring is supported (label 2.4, 7.5) |
| p03 codeine, paracetamol | after retry | same | None | Faithful |
| p04 simvastatin | 1st try | same | "supported by evidence from drug interaction studies": the cited passages do not show amlodipine raising exposure (the label only lists amlodipine for dose modification; the flag reason says it). Minor | — |
| p05 methotrexate | after retry | same | None | Leaves out the label's "primarily at high dose" (patient takes 15 mg weekly), so the PPI risk reads stronger than the label puts it. Renal flag's first passage is the hepatic section (retrieval) |
| p06 clozapine | after retry | same | Smoking "may ... **increase the risk of adverse reactions**": wrong direction. The label says inducers lower clozapine levels; the adverse-reaction risk is when an inducer is stopped | — |
| p07 atorvastatin | after retry | same | None | Leaves out "large quantities, more than 1.2 liters daily" for grapefruit. Summary "ALT ... may be influenced by her diet" is muddled |
| p08 clopidogrel, SSRI | 1st try | same | "**both drugs** interfere with serotonin reuptake": wrong, only sertraline does (label 5.3, Plavix 7.6) | — |
| p09 tramadol, theophylline | after retry | rejected (template): "necessitates a lower starting dose" | None | "Subtherapeutic levels" from higher clearance: a fair reading of the label |
| p10 CYP2C9, CYP2C19 | after retry | rejected (template): "adjusting it according to therapeutic drug monitoring" | None | QT risk comes from the CPIC comment: supported |

### Plain answers: differences from guided
- p02: summary "all of which increase the risk of bleeding", the same wrong direction for vitamin K; same "thrombosis" sentence.
- p04: "due to amlodipine being a **strong CYP3A4 inhibitor**": **invented**, no passage says so.
- p05: calls the eGFR of 55 mL/min/1.73m2 a "creatinine clearance" (wrong measure name).
- p01: "adjusting clopidogrel if necessary", "considering alternative glucose-lowering therapies": accepted by the run's validator, now rejected.

### Validator gaps found (closed, with tests)
1. "necessitates / requires a lower (starting) dose", stated as needed (explanations too).
2. "adjusting it" or "adjusting clopidogrel": the dose word was missing, so the old check missed it.
3. "considering alternative … therapies" in a suggestion: a switch.

### Result
- **No invented drug, dose, number or finding** in any accepted answer.
- **Five statements are wrong or unsupported**: guided p02, p06 and p08, plus plain p02 and p04.
  - Two contradict the evidence: p06 (smoking) and p08 (serotonin reuptake).
  - Two add an unsupported consequence or mechanism: p02 (thrombosis) and plain p04 (CYP3A4 inhibitor).
- None of these is a banned phrase, so no rule can catch them. This is the error type the spec's human check is for.
- Run 1's reading ("no invented claims") does not carry over to this run.

Options, not done yet:
1. **A prompt rule (explain-v3)**: "state only effects the passages state; do not add consequences or mechanisms". This needs a Colab run to measure.
2. **Show the evidence passage next to each explanation in the UI** (already done), so the reader can check it.
