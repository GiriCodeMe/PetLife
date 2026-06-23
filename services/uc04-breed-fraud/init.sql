-- UC-04 Breed Fraud Verification — pgvector schema
-- CRITICAL: embedding dimension MUST be 768 (CLIP ViT-L/14)

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS breed_image_embeddings (
    id          SERIAL PRIMARY KEY,
    policy_id   VARCHAR(64),
    image_hash  VARCHAR(64) UNIQUE,
    embedding   vector(768),          -- MUST be 768, not 512
    breed_label VARCHAR(128),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- IVFFlat index for fast approximate cosine similarity search
CREATE INDEX IF NOT EXISTS breed_emb_idx
    ON breed_image_embeddings
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- Table to track policies with prior fraud flags
CREATE TABLE IF NOT EXISTS policy_fraud_flags (
    id         SERIAL PRIMARY KEY,
    policy_id  VARCHAR(64) UNIQUE NOT NULL,
    fraud_tier INT NOT NULL DEFAULT 1,
    flagged_at TIMESTAMPTZ DEFAULT NOW(),
    notes      TEXT
);

CREATE INDEX IF NOT EXISTS policy_fraud_idx ON policy_fraud_flags (policy_id);
