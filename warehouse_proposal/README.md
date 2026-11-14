# warehouse_proposal

SQL that Person B needs, written as a **proposal for Person A's warehouse migrations** (Person A
owns `warehouse/` in the main repo). Person B applies it locally with `make db-migrate`.

| File | Creates |
|---|---|
| `0001_kb_document_store.sql` | `kb.documents`, `kb.evidence_chunks` (with `vector(768)`), GIN indexes on `drugs`, `class_drugs`, `genes` |

Follows spec p10, with these additions, each needed by Person B's modules: `labeler`,
`headings` (jsonb), `drug_classes`, `class_drugs`, `token_count`, `chunker_version`,
`load_id` (spec: "reload by ID") and `loaded_at`.

**To agree with Person A:** `drugs[]` currently holds lowercase ingredient names
(`clopidogrel`). The spec's retriever passes `flag.drug_ids()`; if the `Flag` contract uses
RxCUIs, `drugs[]` should switch to RxCUIs once `drug_dim` exists. Changing it means reloading
the chunks, but not re-embedding them (vectors don't depend on the tags).
