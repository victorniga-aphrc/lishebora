"""Move operational tables to schema app; add catalog reference tables.

Revision ID: f3c8a91d2e04
Revises: 2c8f4a1b9d0e
Create Date: 2026-04-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

revision: str = "f3c8a91d2e04"
down_revision: Union[str, None] = "2c8f4a1b9d0e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(bind, name: str, schema: str) -> bool:
    insp = sa.inspect(bind)
    return insp.has_table(name, schema=schema)


def upgrade() -> None:
    bind = op.get_bind()

    op.execute(text("CREATE SCHEMA IF NOT EXISTS app"))
    op.execute(text("CREATE SCHEMA IF NOT EXISTS catalog"))

    # --- Operational tables: public -> app ---
    move_order = ("ingredients", "products", "nutrition_data", "product_ingredients", "scans")
    for tbl in move_order:
        if _has_table(bind, tbl, schema="public"):
            op.execute(text(f'ALTER TABLE public."{tbl}" SET SCHEMA app'))

    # --- Reference catalog: catalog.reference_products ---
    op.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS catalog.reference_products (
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
                classification_timestamp TIMESTAMPTZ NULL
            );
            """
        )
    )

    if _has_table(bind, "reference_nutrition_lookup_simulated", schema="public"):
        op.execute(
            text(
                """
                INSERT INTO catalog.reference_products (
                    product_name, energy_kcal, energy_kj, protein_g, carbohydrates_g,
                    total_sugar_g, total_fat_g, fibre_g, sodium_g, sub_type, form,
                    class_name, subclass_name, nova, classification_method,
                    class_confidence, subclass_confidence, nova_confidence, needs_review,
                    classification_timestamp
                )
                SELECT
                    product_name, energy_kcal, energy_kj, protein_g, carbohydrates_g,
                    total_sugar_g, total_fat_g, fibre_g, sodium_g, sub_type, form,
                    class_name, subclass_name, nova, classification_method,
                    class_confidence, subclass_confidence, nova_confidence, needs_review,
                    classification_timestamp
                FROM public.reference_nutrition_lookup_simulated;
                """
            )
        )
        op.execute(text("DROP TABLE public.reference_nutrition_lookup_simulated"))


def downgrade() -> None:
    bind = op.get_bind()

    if _has_table(bind, "reference_products", schema="catalog"):
        op.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS public.reference_nutrition_lookup_simulated (
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
                    classification_timestamp TIMESTAMPTZ NULL
                );
                """
            )
        )
        op.execute(
            text(
                """
                INSERT INTO public.reference_nutrition_lookup_simulated (
                    product_name, energy_kcal, energy_kj, protein_g, carbohydrates_g,
                    total_sugar_g, total_fat_g, fibre_g, sodium_g, sub_type, form,
                    class_name, subclass_name, nova, classification_method,
                    class_confidence, subclass_confidence, nova_confidence, needs_review,
                    classification_timestamp
                )
                SELECT
                    product_name, energy_kcal, energy_kj, protein_g, carbohydrates_g,
                    total_sugar_g, total_fat_g, fibre_g, sodium_g, sub_type, form,
                    class_name, subclass_name, nova, classification_method,
                    class_confidence, subclass_confidence, nova_confidence, needs_review,
                    classification_timestamp
                FROM catalog.reference_products;
                """
            )
        )
        op.execute(text("DROP TABLE catalog.reference_products"))

    # app -> public (reverse dependency order)
    back = ("scans", "product_ingredients", "nutrition_data", "products", "ingredients")
    for tbl in back:
        if _has_table(bind, tbl, schema="app"):
            op.execute(text(f'ALTER TABLE app."{tbl}" SET SCHEMA public'))

    op.execute(text("DROP SCHEMA IF EXISTS app CASCADE"))
    op.execute(text("DROP SCHEMA IF EXISTS catalog CASCADE"))
