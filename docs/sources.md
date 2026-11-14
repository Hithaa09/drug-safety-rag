# Source review

The source list comes from the project document (spec p3–p5). Each source below was **checked
live on 2026-11-11**: whether it can be reached, whether it returns data, and its licence as
stated on its own pages. Recheck before any public or commercial use; licences change.

## Verdict

**Decided 2026-11-14 (Person A, question E1): keep DDInter, the 116 CC BY-NC papers and (E5) the
ClinPGx DPWG data. All three forbid commercial use, so the project must stay non-commercial;
replace them first if that ever changes.**

| Source | Owner | Verdict | Why |
|---|---|---|---|
| DailyMed | B | **Keep (in use)** | Official labels; 24 of 25 loaded and verified |
| French BDPM | B | **Keep (for acenocoumarol; approved by Person A, E2)** | Official regulator. **Licence Ouverte 2.0** (Etalab; corrected 2026-11-14, it was noted as 1.0), which allows adaptation and translation; credit the source and update date. Sintrom 4 mg (CIS 61510352), **ANSM update 20/06/2025** (corrected: noted as June 2018). In French: 13 passages translated, used only after a person reviews each translation |
| CPIC | A (rules), B (evidence text) | **Keep (in use as evidence)** | API works; 17 of 25 drugs have a guideline for a v1 gene (table below). **Licence CC0 1.0** (CPIC's `LICENSE.md`, github.com/cpicpgx/cpic-data, changed 2026-03-09); attribution requested, logo not reusable. 171 recommendations loaded as tier-1 chunks (step 6d) |
| DPWG via ClinPGx | A, B | **Keep (in use as evidence, from 2026-11-14)** | Approved by Person A (E5). Licence: CC BY-SA 4.0 plus share-alike agreement and "no sale for private or commercial use" (`LICENSE.txt` in the ClinPGx download). Loaded only for pairs CPIC doesn't cover: 4 recommendations (acenocoumarol and warfarin with VKORC1, warfarin with CYP2C9). Acenocoumarol + CYP2C9 and clozapine + CYP2D6 are "no action" in DPWG |
| ClinPGx / PharmGKB | A | **Keep** | CC BY-SA 4.0: credit it, and anything we publish from it must use the same licence |
| RxClass (NLM) | B | **Keep (added)** | Drug classes per ingredient (FDA EPC, MED-RT mechanism); free, same terms and attribution as RxNav. Used instead of openFDA's class fields, which were wrong for metformin, amlodipine and paracetamol |
| RxNorm / RxNav | A | **Keep** | No licence needed for the API; 20 requests/s; NLM attribution statement required |
| FDA CYP table | A | **Keep** | Updated 05/29/2026; lists smoking (CYP1A2 inducer), grapefruit and St. John's wort; FDA calls it "examples, not a complete list". The site blocks scripts, so copy it into a seed CSV by hand (as the spec says) |
| PMC Open Access | B | **Keep** | Works (E-utilities and BioC tested). Licences vary by article, so store each one. **Allowed retrieval is now: Cloud, OAI-PMH, E-utilities, BioC. FTP is no longer listed** (the spec still lists it) |
| DDInter 2.0 | A | **Keep (decided 2026-11-14, E1: the project stays non-commercial)** | CSV downloads exist (this resolves the spec's "no bulk download" risk). Licence CC BY-NC-SA 4.0; terms allow "personal, non-commercial, informational or scholarly use" only. **Must be replaced if the project is or becomes commercial** |
| openFDA | B | **Keep, for its extra fields only** | CC0, the most open licence. Label *text* is a duplicate: 23 of our 24 labels are there at the same version as DailyMed (paracetamol's OTC label and acenocoumarol are missing). Its harmonised fields are unique: application number and manufacturer (to replace repackager copies with the original manufacturer's label), and RxCUI, NDC, brand and UNII (cross-check for Person A). `is_original_packager` was blank on the labels checked, so don't rely on it. **Its drug-class fields were tested and rejected** (wrong or empty for several drugs); classes come from RxClass. Its other datasets (adverse-event reports, recalls) don't fit: unverified and not causal |
| FooDrugs | A/B | **Drop for v1** | Licence is fine (CC BY 4.0 on Zenodo), but the data is 3.4 million *potential* interactions, mostly text-mined or inferred, a 3.9 GB SQL dump from 2022. Too unreliable for rules; label text and the FDA table already cover grapefruit, smoking, alcohol and vitamin K |
| DrugBank | — | **Drop** | Academic downloads are paused; the site blocks automated access (HTTP 403); CC BY-NC 4.0. The spec already says not to depend on it |

## CPIC coverage of the 25 v1 drugs (live, 574 gene–drug pairs)

| Drug | CPIC level for v1 genes | CPIC guideline |
|---|---|---|
| clopidogrel | CYP2C19 A | CYP2C19 and Clopidogrel |
| omeprazole, pantoprazole | CYP2C19 A | CYP2C19 and Proton Pump Inhibitors |
| warfarin | CYP2C9 A, VKORC1 A | CYP2C9, VKORC1, CYP4F2 and Warfarin |
| ibuprofen, celecoxib | CYP2C9 A | CYP2C9 and NSAIDs |
| aspirin, diclofenac | CYP2C9 C | CYP2C9 and NSAIDs |
| simvastatin, atorvastatin, rosuvastatin | SLCO1B1 A | SLCO1B1, ABCG2, CYP2C9 and Statins |
| phenytoin | CYP2C9 A | CYP2C9, HLA-B and Phenytoin |
| codeine, tramadol | CYP2D6 A | CYP2D6, OPRM1, COMT and Opioids |
| amitriptyline | CYP2C19 A, CYP2D6 A | Tricyclic Antidepressants |
| sertraline, citalopram | CYP2C19 A, CYP2D6 C | Serotonin Reuptake Inhibitors |
| acenocoumarol | CYP2C9 B | **none**; use DPWG |
| clozapine | CYP2D6 B/C | none |
| methotrexate | SLCO1B1 C | none |
| metformin, amlodipine, theophylline, paracetamol, allopurinol | — | none for v1 genes |

The drugs with no gene guideline are still useful: they supply the lab, lifestyle and interaction
test cases (metformin and eGFR, theophylline and clozapine with smoking, paracetamol with alcohol
and the liver, methotrexate and allopurinol with kidney function).

**Gap in the v1 gene list:** CPIC rates **HLA-B** at level A for allopurinol (HLA-B\*58:01) and
phenytoin (HLA-B\*15:02), which flag the risk of severe skin reactions. HLA-B isn't one of the
5 v1 genes, so those flags can't fire. ABCG2 (rosuvastatin, level A) and CYP4F2 (warfarin,
level A) are also outside the list. Adding genes is a scope decision for Persons A and B.

## Links checked

- DDInter terms: https://ddinter2.scbdd.com/terms/ and downloads: https://ddinter2.scbdd.com/download/
- openFDA licence: https://open.fda.gov/license/
- RxClass API: https://rxnav.nlm.nih.gov/REST/rxclass/class/byDrugName.json
- RxNav terms: https://lhncbc.nlm.nih.gov/RxNav/TermsofService.html
- PMC Open Access: https://pmc.ncbi.nlm.nih.gov/tools/openftlist/
- CPIC API: https://api.cpicpgx.org/v1/pair_view (the CPIC licence page redirects to the ClinPGx data usage policy)
- ClinPGx data usage: https://www.clinpgx.org/page/dataUsagePolicy (the page needs a browser; the CC BY-SA 4.0 statement was confirmed from the ClinPGx site and API docs)
- FooDrugs on Zenodo: https://zenodo.org/records/8192515
- DrugBank downloads: https://go.drugbank.com/downloads
- FDA CYP table: https://www.fda.gov/drugs/drug-interactions-labeling/healthcare-professionals-fdas-examples-drugs-interact-cyp-enzymes-and-transporter-systems
- French BDPM: https://base-donnees-publique.medicaments.gouv.fr/
