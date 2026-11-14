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

## Run 2 (pending): prompt `explain-v2`, with CPIC evidence
To be filled in after the next Colab run, using `review.md` in that run's folder.
