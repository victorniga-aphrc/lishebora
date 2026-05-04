"""Add catalog.classification_cache for OpenAI runtime classifier results.

Revision ID: f1a2b3c4d5e6
Revises: d8e9f0a1b2c3
Create Date: 2026-05-04

The runtime classifier (formerly BiLSTM, now OpenAI) writes its predictions to this
table keyed by a normalized name+brand cache key so that:
  - repeated scans of the same product are served from cache (no LLM call cost/latency)
  - predictions are auditable (model used, confidence, reason)
  - operators can inspect / override misclassifications post-hoc
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create catalog.classification_cache."""
    bind = op.get_bind()
    insp = sa.inspect(bind)

    if insp.has_table("classification_cache", schema="catalog"):
        print("catalog.classification_cache already exists - skipping create")
        return

    print("Creating catalog.classification_cache...")
    op.execute(
        text(
            """
            CREATE TABLE catalog.classification_cache (
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

            CREATE UNIQUE INDEX idx_classification_cache_key
                ON catalog.classification_cache (cache_key);
            CREATE INDEX idx_classification_cache_class
                ON catalog.classification_cache (class_name, subclass_name);
            CREATE INDEX idx_classification_cache_review
                ON catalog.classification_cache (needs_review)
                WHERE needs_review = TRUE;

            COMMENT ON TABLE catalog.classification_cache IS
                'Runtime classifier (OpenAI) predictions cached by normalized product key';
            COMMENT ON COLUMN catalog.classification_cache.confidence IS
                'Self-reported model confidence 1-5; 5 = definitely correct';
            COMMENT ON COLUMN catalog.classification_cache.source IS
                'openai | bilstm | manual - which classifier produced this row';
            """
        )
    )
    print("Migration complete.")


def downgrade() -> None:
    """Drop catalog.classification_cache."""
    print("Dropping catalog.classification_cache...")
    op.execute(text("DROP TABLE IF EXISTS catalog.classification_cache CASCADE"))
