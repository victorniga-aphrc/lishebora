"""Idempotently create catalog.classification_cache (mirrors the Alembic migration).

Use this when running Alembic CLI is awkward; it executes the same DDL directly.
"""
from __future__ import annotations

import os
import sys

import psycopg2
from dotenv import load_dotenv


DDL = """
CREATE TABLE IF NOT EXISTS catalog.classification_cache (
    id SERIAL PRIMARY KEY,
    cache_key TEXT NOT NULL,
    product_name TEXT NOT NULL,
    brand TEXT NULL,
    class_name TEXT NULL,
    subclass_name TEXT NULL,
    nova TEXT NULL,
    confidence SMALLINT NULL,
    needs_review BOOLEAN NOT NULL DEFAULT FALSE,
    reason TEXT NULL,
    model_used TEXT NULL,
    source TEXT NOT NULL DEFAULT 'openai',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_classification_cache_key
    ON catalog.classification_cache (cache_key);
CREATE INDEX IF NOT EXISTS idx_classification_cache_class
    ON catalog.classification_cache (class_name, subclass_name);
CREATE INDEX IF NOT EXISTS idx_classification_cache_review
    ON catalog.classification_cache (needs_review)
    WHERE needs_review = TRUE;

COMMENT ON TABLE catalog.classification_cache IS
    'Runtime classifier (OpenAI) predictions cached by normalized product key';
"""


def main() -> int:
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        return 1

    if url.startswith("postgresql+psycopg2://"):
        url = "postgresql://" + url.split("://", 1)[1]

    conn = psycopg2.connect(url)
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute("CREATE SCHEMA IF NOT EXISTS catalog;")
            cur.execute(DDL)
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'catalog' AND table_name = 'classification_cache'
                );
                """
            )
            exists = cur.fetchone()[0]
        print(f"OK: catalog.classification_cache exists = {exists}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
