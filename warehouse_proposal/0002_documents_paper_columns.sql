-- PROPOSAL for Person A's warehouse: identifiers for research papers in kb.documents.
-- Spec p10 lists external_id (set ID, PMCID, PMID); papers have all three, plus a journal.
ALTER TABLE kb.documents ADD COLUMN IF NOT EXISTS pmid    text;
ALTER TABLE kb.documents ADD COLUMN IF NOT EXISTS doi     text;
ALTER TABLE kb.documents ADD COLUMN IF NOT EXISTS journal text;
CREATE INDEX IF NOT EXISTS documents_pmid ON kb.documents (pmid);
