"""Write-through updates to ``catalog.product_nutrition`` after each successful extract.

When a user scans a product:
- If the product already exists (by normalized food name) → fill only NULL columns
- Otherwise → INSERT a new row with the scan data

Note: The new ``catalog.product_nutrition`` schema does not have classification metadata
columns (classification_method, needs_review, etc.) - those have been removed from the
production schema. Scans only update nutritional and classification fields.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import OcrResult
from app.services.reference_catalog_db import find_exact_reference_row
from app.utils.nova_display import normalize_nova_for_api

logger = logging.getLogger(__name__)


def _product_nutrition_table() -> str:
    """Quoted schema.table for the PRIMARY product nutrition table."""
    return settings.reference_catalog_qualified_sql


def _octagon_count(ocr: OcrResult) -> int:
    k = ocr.knpm_label
    if not k or not k.octagons:
        return 0
    return len(k.octagons)


def _nova_for_catalog(ocr: OcrResult) -> str | None:
    pc = ocr.product_classification
    if pc and pc.nova and str(pc.nova).strip():
        return normalize_nova_for_api(str(pc.nova).strip())
    cp = ocr.classifier_prediction
    if cp and cp.nova and str(cp.nova).strip():
        return normalize_nova_for_api(str(cp.nova).strip())
    return None


def upsert_reference_product_from_ocr(db: Session, ocr: OcrResult) -> None:
    """
    Write-through update to ``catalog.product_nutrition`` after a successful pipeline run.

    - If a row exists with the **same normalized** ``food_name`` as ``ocr``,
      **fill only NULL** columns from this scan (nutrients, taxonomy, NOVA, octagons).
    - Otherwise **insert** a new row.

    Skips when ``product_info`` / name is missing. On SQL failure: logs at ERROR with traceback
    and re-raises. Callers that must still commit other work (e.g. ``product_scan_summary``)
    should invoke this inside ``Session.begin_nested()`` so the savepoint absorbs the failure.
    """
    pi = ocr.product_info
    if pi is None or not (pi.name or "").strip():
        return

    nut = ocr.nutrition_per_100g
    fat = float(nut.total_fat) if nut and nut.total_fat is not None else None
    sugar = float(nut.total_sugar) if nut and nut.total_sugar is not None else None
    sodium = float(nut.sodium) if nut and nut.sodium is not None else None
    nova = _nova_for_catalog(ocr)
    cls_name = ocr.class_name
    sub_name = ocr.subclass_name
    oct_n = _octagon_count(ocr)
    ts = datetime.utcnow()
    tbl = _product_nutrition_table()

    try:
        existing = find_exact_reference_row(db, pi)
        if existing is not None:
            key_name = str(existing.get("food_name") or pi.name.strip())
            sql = text(
                f"""
                UPDATE {tbl} SET
                    fat_g = COALESCE(fat_g, CAST(:fat_g AS double precision)),
                    sugar_g = COALESCE(sugar_g, CAST(:sugar_g AS double precision)),
                    sodium_g = COALESCE(sodium_g, CAST(:sodium_g AS double precision)),
                    class_name = COALESCE(class_name, :class_name),
                    subclass_name = COALESCE(subclass_name, :subclass_name),
                    nova = COALESCE(nova, :nova),
                    octagon_count = COALESCE(octagon_count, CAST(:octagon_count AS integer)),
                    updated_at = CAST(:ts AS timestamptz)
                WHERE food_name = :key_name
                """
            )
            db.execute(
                sql,
                {
                    "fat_g": fat,
                    "sugar_g": sugar,
                    "sodium_g": sodium,
                    "class_name": cls_name,
                    "subclass_name": sub_name,
                    "nova": nova,
                    "octagon_count": oct_n,
                    "ts": ts,
                    "key_name": key_name,
                },
            )
            return

        # New product - need class_name and subclass_name (NOT NULL constraints)
        if not cls_name or not sub_name:
            logger.info(
                "Skipping insert into product_nutrition: missing classification "
                "(food_name=%r class=%r subclass=%r)",
                pi.name.strip()[:100],
                cls_name,
                sub_name,
            )
            return

        insert = text(
            f"""
            INSERT INTO {tbl} (
                food_name,
                fat_g, sugar_g, sodium_g,
                class_name, subclass_name, nova, octagon_count,
                created_at, updated_at
            ) VALUES (
                :food_name,
                CAST(:fat_g AS double precision),
                CAST(:sugar_g AS double precision),
                CAST(:sodium_g AS double precision),
                :class_name, :subclass_name, :nova, CAST(:octagon_count AS integer),
                CAST(:ts AS timestamptz), CAST(:ts AS timestamptz)
            )
            ON CONFLICT (LOWER(TRIM(food_name))) DO NOTHING
            """
        )
        db.execute(
            insert,
            {
                "food_name": pi.name.strip(),
                "fat_g": fat,
                "sugar_g": sugar,
                "sodium_g": sodium,
                "class_name": cls_name,
                "subclass_name": sub_name,
                "nova": nova,
                "octagon_count": oct_n,
                "ts": ts,
            },
        )
    except SQLAlchemyError:
        logger.exception(
            "catalog.product_nutrition upsert failed (food_name=%r)",
            (pi.name or "").strip()[:200],
        )
        raise
