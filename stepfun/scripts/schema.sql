CREATE DATABASE IF NOT EXISTS stepfun_app;
USE stepfun_app;

CREATE TABLE IF NOT EXISTS docs (
  kb VARCHAR(64) NOT NULL COMMENT 'knowledge base / collection name',
  id VARCHAR(128) NOT NULL COMMENT 'doc_id:chunk_idx',
  doc_name VARCHAR(255) COMMENT 'original document name',
  chunk_idx INT COMMENT 'chunk index within document',
  content TEXT COMMENT 'chunk text (fulltext source)',
  embedding VECTOR(384) COMMENT 'browser-computed embedding, cosine-normalized',
  PRIMARY KEY (kb, id),
  FULLTEXT INDEX idx_fts (content) WITH PARSER ik,
  VECTOR INDEX idx_vec (embedding) WITH (DISTANCE=cosine, TYPE=hnsw, LIB=vsag)
) ORGANIZATION = HEAP;

CREATE TABLE IF NOT EXISTS kb_state (
  kb_name VARCHAR(64) PRIMARY KEY,
  doc_count INT DEFAULT 0,
  chunk_count INT DEFAULT 0,
  model_dtype VARCHAR(16) COMMENT 'fp32, fp16 or q8',
  infer_backend VARCHAR(16) COMMENT 'webgpu or wasm',
  updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ORGANIZATION = HEAP;
