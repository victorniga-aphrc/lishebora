"""Write-through updates to ``catalog.reference_products`` after each successful extract."""

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


def _reference_table_sql() -> str:
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
    b = ocr.foodclasses_bilstm_prediction
    if b and b.nova_label and str(b.nova_label).strip():
        return normalize_nova_for_api(str(b.nova_label).strip())
    return None


def upsert_reference_product_from_ocr(db: Session, ocr: OcrResult) -> None:
    """
    After a successful pipeline run:

    - If a catalog row exists with the **same normalized** ``product_name`` as ``ocr``,
      **fill only NULL** columns from this scan (nutrients, taxonomy, NOVA, method flag).
    - Otherwise **insert** a new row (``classification_method='scan_extract'``,
      ``needs_review=true``).

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
    tbl = _reference_table_sql()

    try:
        existing = find_exact_reference_row(db, pi)
        if existing is not None:
            key_name = str(existing.get("product_name") or pi.name.strip())
            sql = text(
                f"""
                UPDATE {tbl} SET
                    total_fat_g = COALESCE(total_fat_g, CAST(:total_fat_g AS double precision)),
                    total_sugar_g = COALESCE(total_sugar_g, CAST(:total_sugar_g AS double precision)),
                    sodium_g = COALESCE(sodium_g, CAST(:sodium_g AS double precision)),
                    class_name = COALESCE(class_name, :class_name),
                    subclass_name = COALESCE(subclass_name, :subclass_name),
                    nova = COALESCE(nova, :nova),
                    octagon_count = COALESCE(octagon_count, CAST(:octagon_count AS integer)),
                    classification_method = COALESCE(classification_method, :scan_method),
                    needs_review = COALESCE(needs_review, CAST(:needs_review AS boolean)),
                    classification_timestamp = COALESCE(
                        classification_timestamp, CAST(:ts AS timestamptz)
                    )
                WHERE product_name = :key_name
                """
            )
            db.execute(
                sql,
                {
                    "total_fat_g": fat,
                    "total_sugar_g": sugar,
                    "sodium_g": sodium,
                    "class_name": cls_name,
                    "subclass_name": sub_name,
                    "nova": nova,
                    "octagon_count": oct_n,
                    "scan_method": "scan_extract",
                    "needs_review": True,
                    "ts": ts,
                    "key_name": key_name,
                },
            )
            return

        insert = text(
            f"""
            INSERT INTO {tbl} (
                product_name,
                total_fat_g, total_sugar_g, sodium_g,
                class_name, subclass_name, nova, octagon_count,
                classification_method, needs_review, classification_timestamp
            ) VALUES (
                :product_name,
                CAST(:total_fat_g AS double precision),
                CAST(:total_sugar_g AS double precision),
                CAST(:sodium_g AS double precision),
                :class_name, :subclass_name, :nova, CAST(:octagon_count AS integer),
                :scan_method, CAST(:needs_review AS boolean), CAST(:ts AS timestamptz)
            )
            """
        )
        db.execute(
            insert,
            {
                "product_name": pi.name.strip(),
                "total_fat_g": fat,
                "total_sugar_g": sugar,
                "sodium_g": sodium,
                "class_name": cls_name,
                "subclass_name": sub_name,
                "nova": nova,
                "octagon_count": oct_n,
                "scan_method": "scan_extract",
                "needs_review": True,
                "ts": ts,
            },
        )
    except SQLAlchemyError:
        logger.exception(
            "catalog.reference_products upsert failed (product_name=%r)",
            (pi.name or "").strip()[:200],
        )
        raise
