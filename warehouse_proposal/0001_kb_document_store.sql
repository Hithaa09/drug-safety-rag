-- PROPOSAL for Person A's warehouse migrations: the document store (spec p10).
-- Person B uses this locally; Person A copies it into warehouse/migrations/ when agreed.
-- Safe to rerun.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS kb;

-- One row per label, guideline or paper. Raw files stay on disk (raw_path).
CREATE TABLE IF NOT EXISTS kb.documents (
    doc_id          text PRIMARY KEY,               -- {source}:{external_id}
    source          text        NOT NULL,           -- dailymed, pmc, bdpm, ...
    external_id     text        NOT NULL,           -- set ID, PMCID, PMID
    title           text,
    labeler         text,
    drugs           text[]      NOT NULL DEFAULT '{}',
    genes           text[]      NOT NULL DEFAULT '{}',
    study_type      text,                           -- label, guideline, review, trial, ...
    tier            smallint    NOT NULL CHECK (tier BETWEEN 1 AND 4),
    pub_year        integer,
    license         text        NOT NULL,
    raw_path        text,
    source_version  text,
    retrieved_at    timestamptz,
    load_id         text        NOT NULL,
    UNIQUE (source, external_id)
);

-- The only table the RAG layer searches by similarity.
CREATE TABLE IF NOT EXISTS kb.evidence_chunks (
    chunk_id        text PRIMARY KEY,               -- {doc_id}:{section}:{n}
    doc_id          text        NOT NULL REFERENCES kb.documents(doc_id) ON DELETE CASCADE,
    source          text        NOT NULL,
    tier            smallint    NOT NULL CHECK (tier BETWEEN 1 AND 4),
    section         text        NOT NULL,
    headings        jsonb       NOT NULL,
    text            text        NOT NULL,
    drugs           text[]      NOT NULL,           -- exact drug filter (spec RAG step 2)
    genes           text[]      NOT NULL DEFAULT '{}',
    drug_classes    text[]      NOT NULL DEFAULT '{}',
    class_drugs     text[]      NOT NULL DEFAULT '{}',  -- fallback filter via drug classes
    token_count     integer     NOT NULL,
    source_version  text,
    license         text        NOT NULL,
    chunker_version text        NOT NULL,
    embedding       vector(768) NOT NULL,           -- size must match embedding_model
    embedding_model text        NOT NULL,
    load_id         text        NOT NULL,
    loaded_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS evidence_chunks_drugs_gin       ON kb.evidence_chunks USING gin (drugs);
CREATE INDEX IF NOT EXISTS evidence_chunks_class_drugs_gin ON kb.evidence_chunks USING gin (class_drugs);
CREATE INDEX IF NOT EXISTS evidence_chunks_genes_gin       ON kb.evidence_chunks USING gin (genes);
CREATE INDEX IF NOT EXISTS evidence_chunks_doc_id          ON kb.evidence_chunks (doc_id);
CREATE INDEX IF NOT EXISTS evidence_chunks_load_id         ON kb.evidence_chunks (load_id);
-- Spec: add an HNSW index (vector_cosine_ops) only if searches turn out slow.
