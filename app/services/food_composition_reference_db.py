"""Secondary food composition lookup from ``catalog.food_composition_reference``.

This service is the SECONDARY fallback used ONLY when the primary
``catalog.product_nutrition`` table cannot find a match. It contains 654 standardized
food composition entries (raw foods, prepared dishes, generic items).

Lookup strategy when called:
1. Fuzzy name match against the generic food names
2. (Optional) Category-based average lookup using class+subclass

Important caveats:
- Sugar values are NOT available (the source data only had fat/sodium for these entries).
  Returned NutritionData will have ``total_sugar = None``.
- Match scores tend to be lower than primary because the names are generic
  ("Beans broad dry raw" vs a retail SKU "Royco Garden Beans Tin").
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine
from app.models import NutritionData, ReferenceNutritionMatch
from app.utils.pack_description import normalize_pack_description

logger = logging.getLogger(__name__)


def _food_composition_table() -> str:
    """Quoted schema.table for the SECONDARY food composition reference table."""
    return settings.food_composition_reference_qualified_sql


def _score(a: str, b: str) -> float:
    na = normalize_pack_description(a)
    nb = normalize_pack_description(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio() * 100.0


def _all_reference_rows(db: Session | None) -> list[dict[str, Any]]:
    """Fetch all rows from catalog.food_composition_reference."""
    sql = text(
        f"""
        SELECT food_name, sugar_g, fat_g, sodium_g,
               class_name, subclass_name, nova
        FROM {_food_composition_table()}
        """
    )
    try:
        if db is not None:
            return [dict(r._mapping) for r in db.execute(sql).fetchall()]
        with engine.begin() as conn:
            return [dict(r._mapping) for r in conn.execute(sql).fetchall()]
    except SQLAlchemyError:
        logger.exception("Failed to query food_composition_reference")
        return []


def _query_reference_by_classification(
    class_name: Optional[str],
    subclass_name: Optional[str],
    db: Session | None,
) -> list[dict[str, Any]]:
    """Fetch reference rows matching the given class+subclass."""
    if not class_name or not subclass_name:
        return []
    sql = text(
        f"""
        SELECT food_name, sugar_g, fat_g, sodium_g, class_name, subclass_name, nova
        FROM {_food_composition_table()}
        WHERE class_name = :class_name AND subclass_name = :subclass_name
        """
    )
    params = {"class_name": class_name, "subclass_name": subclass_name}
    try:
        if db is not None:
            return [dict(r._mapping) for r in db.execute(sql, params).fetchall()]
        with engine.begin() as conn:
            return [dict(r._mapping) for r in conn.execute(sql, params).fetchall()]
    except SQLAlchemyError:
        logger.exception(
            "Failed to query food_composition_reference for class=%r subclass=%r",
            class_name,
            subclass_name,
        )
        return []


def _row_to_nutrition(row: dict[str, Any]) -> Optional[NutritionData]:
    """Convert a reference row to NutritionData. Note: sugar is always None for reference."""
    fat = row.get("fat_g")
    sodium = row.get("sodium_g")
    sugar = row.get("sugar_g")  # Will typically be None
    if fat is None and sodium is None and sugar is None:
        return None
    return NutritionData(
        total_fat=fat,
        trans_fat=None,
        total_sugar=sugar,
        sodium=sodium,
    )


def _average_nutrient(rows: list[dict[str, Any]], key: str) -> Optional[float]:
    """Calculate the average of a nutrient across reference rows (excluding nulls)."""
    values = [r.get(key) for r in rows if r.get(key) is not None]
    if not values:
        return None
    return sum(values) / len(values)


def lookup_nutrition_in_food_composition_reference(
    target: str,
    threshold: float,
    db: Session | None = None,
) -> tuple[Optional[NutritionData], Optional[ReferenceNutritionMatch]]:
    """
    Fallback nutrition lookup in ``catalog.food_composition_reference`` by name match.
    
    Used when the primary ``catalog.product_nutrition`` lookup completely missed.
    Tries exact normalized name match first, then fuzzy match above ``threshold``.
    
    Args:
        target: The product query text (composed name + brand)
        threshold: Minimum fuzzy match score to accept (0-100)
        db: Optional database session
        
    Returns:
        Tuple of (NutritionData or None, ReferenceNutritionMatch or None).
        Sugar will always be None (reference table doesn't have sugar values).
    """
    if not target:
        return None, None
    rows = _all_reference_rows(db)
    if not rows:
        return None, None
    
    target_norm = normalize_pack_description(target)
    
    # Try exact normalized name match
    exact = next(
        (
            r
            for r in rows
            if normalize_pack_description(str(r.get("food_name") or "")) == target_norm
        ),
        None,
    )
    if exact is not None:
        nut = _row_to_nutrition(exact)
        if nut is not None:
            logger.debug(
                "Secondary fallback hit (exact): target=%r matched=%r",
                target[:80],
                str(exact.get("food_name"))[:80],
            )
            return nut, ReferenceNutritionMatch(
                matched_product_name=str(exact.get("food_name") or target),
                match_method="reference_exact_name",
                match_score=None,
                sub_type=None,
                form=None,
            )
    
    # Try fuzzy match
    best: Optional[dict[str, Any]] = None
    best_score = 0.0
    for r in rows:
        s = _score(target, str(r.get("food_name") or ""))
        if s > best_score:
            best = r
            best_score = s
    if best is None or best_score < threshold:
        return None, None
    nut = _row_to_nutrition(best)
    if nut is None:
        return None, None
    logger.debug(
        "Secondary fallback hit (fuzzy %.1f): target=%r matched=%r",
        best_score,
        target[:80],
        str(best.get("food_name"))[:80],
    )
    return nut, ReferenceNutritionMatch(
        matched_product_name=str(best.get("food_name") or target),
        match_method="reference_fuzzy_name",
        match_score=best_score,
        sub_type=None,
        form=None,
    )


def lookup_food_composition_by_classification(
    class_name: Optional[str],
    subclass_name: Optional[str],
    db: Session | None = None,
) -> Optional[NutritionData]:
    """
    Category-based fallback: average nutrient values for a given (class, subclass).
    
    Used as a deeper fallback when neither primary nor secondary name match worked,
    but a classification (from the BiLSTM or another source) is available.
    Returns None if classification is missing or no rows match.
    
    Note: Sugar is always None (reference table doesn't have sugar values).
    """
    if not class_name or not subclass_name:
        return None
    rows = _query_reference_by_classification(class_name, subclass_name, db)
    if not rows:
        return None
    avg_fat = _average_nutrient(rows, "fat_g")
    avg_sodium = _average_nutrient(rows, "sodium_g")
    if avg_fat is None and avg_sodium is None:
        return None
    return NutritionData(
        total_fat=avg_fat,
        trans_fat=None,
        total_sugar=None,
        sodium=avg_sodium,
    )
