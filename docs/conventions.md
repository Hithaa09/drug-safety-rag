# Conventions (Person B)

The formats Person B's modules write. Agree them with Person A before any data is loaded; changing
an ID format later means reloading.

## IDs

| ID | Format | Example |
|---|---|---|
| `doc_id` | `{source}:{external_id}` | `dailymed:<setid>`, `pmc:PMC1234567` |
| `chunk_id` | `{doc_id}:{section}:{n:03d}`, n counted per section in document order | `dailymed:<setid>:drug_interactions:003` |
| `load_id` | `{source}-{YYYYMMDDTHHMMSS}` | `dailymed-20261112T101500` |

`source` values written by Person B: `dailymed`, `openfda`, `pmc`, `pubmed`, `cpic_guideline`,
`curated`.

Read from Person A (spec p14): `flag_id` such as `gene-001`; `rule_id` such as
`gene_drug:CYP2C19:poor_metabolizer:clopidogrel`.

## Names

- Drugs: lowercase ingredient names (`clopidogrel`); joins use the ingredient RxCUI.
- Genes: uppercase symbols (`CYP2C19`).

## Evidence tiers

`1` guidelines and labels · `2` systematic reviews and meta-analyses · `3` trials and large
cohorts · `4` case reports (only when a drug has nothing better).

## Severity (for curated rows)

Highest first: `contraindicated`, `major`, `moderate`, `minor`. Keep the source's original wording
in `severity_source`.

## Every row Person B writes carries

`source`, `source_version`, `license`, `retrieved_at`, `load_id`; curated rule rows also carry
`evidence_chunk_id`.

## Files

- Raw files: `data/raw/<source>/<external_id>/v<version>.<ext>`, never edited or overwritten.
- Parsed files: `data/processed/<source>/<external_id>.json`.
- Run outputs: `data/outputs/YYYY-MM-DD_<what>/`. No `_v1` / `_v2` folder copies.
