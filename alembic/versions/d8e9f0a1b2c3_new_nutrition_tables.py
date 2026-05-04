"""Replace catalog.reference_products with new product_nutrition and food_composition_reference tables.

Revision ID: d8e9f0a1b2c3
Revises: a3b4c5d6e7f8
Create Date: 2026-05-04

This migration:
1. Drops the old catalog.reference_products table (if exists)
2. Creates catalog.product_nutrition (3,973 SKU products with octagons)
3. Creates catalog.food_composition_reference (654 reference foods)
4. Adds indexes for performance

After migration, run scripts/load_nutrition_data.py to populate tables from CSVs.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, None] = "a3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create new nutrition catalog tables."""
    bind = op.get_bind()
    insp = sa.inspect(bind)
    
    # Drop old reference_products table if exists
    if insp.has_table("reference_products", schema="catalog"):
        print("Dropping old catalog.reference_products table...")
        op.execute(text("DROP TABLE catalog.reference_products CASCADE"))
    
    # Create product_nutrition table (primary SKU products with octagons)
    print("Creating catalog.product_nutrition...")
    op.execute(
        text(
            """
            CREATE TABLE catalog.product_nutrition (
                id SERIAL PRIMARY KEY,
                food_name TEXT NOT NULL,
                sugar_g DOUBLE PRECISION NULL,
                fat_g DOUBLE PRECISION NULL,
                sodium_g DOUBLE PRECISION NULL,
                class_name TEXT NOT NULL,
                subclass_name TEXT NOT NULL,
                nova TEXT NULL,
                octagon_count INTEGER NOT NULL DEFAULT 0,
                
                -- Metadata
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            
            -- Indexes for performance
            CREATE INDEX idx_product_nutrition_food_name ON catalog.product_nutrition 
                USING gin(to_tsvector('english', food_name));
            CREATE INDEX idx_product_nutrition_class ON catalog.product_nutrition (class_name, subclass_name);
            CREATE INDEX idx_product_nutrition_octagon ON catalog.product_nutrition (octagon_count);
            CREATE INDEX idx_product_nutrition_nova ON catalog.product_nutrition (nova);
            
            -- Unique constraint on normalized food name (for fuzzy matching)
            CREATE UNIQUE INDEX idx_product_nutrition_food_name_lower 
                ON catalog.product_nutrition (LOWER(TRIM(food_name)));
            
            COMMENT ON TABLE catalog.product_nutrition IS 
                'Retail product nutrition data (3,973 SKUs) with KNPM octagon warnings';
            COMMENT ON COLUMN catalog.product_nutrition.octagon_count IS 
                'Number of KNPM warning octagons (0-3) based on sugar/fat/sodium thresholds';
            """
        )
    )
    
    # Create food_composition_reference table (reference foods)
    print("Creating catalog.food_composition_reference...")
    op.execute(
        text(
            """
            CREATE TABLE catalog.food_composition_reference (
                id SERIAL PRIMARY KEY,
                food_name TEXT NOT NULL,
                sugar_g DOUBLE PRECISION NULL,
                fat_g DOUBLE PRECISION NULL,
                sodium_g DOUBLE PRECISION NULL,
                class_name TEXT NOT NULL,
                subclass_name TEXT NOT NULL,
                nova TEXT NULL,
                
                -- Metadata
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            );
            
            -- Indexes for performance
            CREATE INDEX idx_food_composition_food_name ON catalog.food_composition_reference 
                USING gin(to_tsvector('english', food_name));
            CREATE INDEX idx_food_composition_class ON catalog.food_composition_reference (class_name, subclass_name);
            CREATE INDEX idx_food_composition_nova ON catalog.food_composition_reference (nova);
            
            -- Unique constraint on normalized food name
            CREATE UNIQUE INDEX idx_food_composition_food_name_lower 
                ON catalog.food_composition_reference (LOWER(TRIM(food_name)));
            
            COMMENT ON TABLE catalog.food_composition_reference IS 
                'Standard food composition reference data (654 foods) for nutrient lookup and imputation';
            """
        )
    )
    
    print("Migration complete. Run scripts/load_nutrition_data.py to populate tables.")


def downgrade() -> None:
    """Drop new tables and restore old reference_products structure."""
    print("Dropping new nutrition tables...")
    op.execute(text("DROP TABLE IF EXISTS catalog.food_composition_reference CASCADE"))
    op.execute(text("DROP TABLE IF EXISTS catalog.product_nutrition CASCADE"))
    
    # Recreate old reference_products table structure (empty)
    print("Recreating old catalog.reference_products structure...")
    op.execute(
        text(
            """
            CREATE TABLE catalog.reference_products (
                product_name TEXT,
                energy_kcal DOUBLE PRECISION NULL,
                energy_kj DOUBLE PRECISION NULL,
                protein_g DOUBLE PRECISION NULL,
                carbohydrates_g DOUBLE PRECISION NULL,
                total_sugar_g DOUBLE PRECISION NULL,
                total_fat_g DOUBLE PRECISION NULL,
                fibre_g DOUBLE PRECISION NULL,
                sodium_g DOUBLE PRECISION NULL,
                sub_type TEXT NULL,
                form TEXT NULL,
                class_name TEXT NULL,
                subclass_name TEXT NULL,
                nova TEXT NULL,
                classification_method TEXT NULL,
                class_confidence DOUBLE PRECISION NULL,
                subclass_confidence DOUBLE PRECISION NULL,
                nova_confidence DOUBLE PRECISION NULL,
                needs_review BOOLEAN NULL,
                classification_timestamp TIMESTAMPTZ NULL,
                octagon_count INTEGER NULL
            );
            """
        )
    )
    print("Downgrade complete (data not restored - please restore from backup if needed).")
