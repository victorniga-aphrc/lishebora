"""PostgreSQL-backed catalog lookups for the active pipeline.

Two-table fallback strategy:

1. PRIMARY: ``catalog.product_nutrition`` (3,973 retail SKUs)
   - First lookup by exact normalized food name, then fuzzy match
   - If a hit is found, use that row (NULL nutrients stay NULL)

2. SECONDARY: ``catalog.food_composition_reference`` (654 standard foods)
   - Used ONLY when primary completely misses (no exact, no fuzzy hit)
   - Tries fuzzy name match first, then category-based average if classification is known
   - Provides fat/sodium only (reference table has no sugar column)

Public API (kept stable for callers):
    - lookup_reference_nutrition_db(...)
    - lookup_product_classification_db(...)
    - find_exact_reference_row(...)
    - iter_reference_products_with_nutrition_db()
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine
from app.models import (
    NutritionData,
    ProductInfo,
    ProductClassification,
    ReferenceNutritionMatch,
)
from app.services.food_composition_reference_db import (
    lookup_nutrition_in_food_composition_reference,
)
from app.utils.nova_display import normalize_nova_for_api
from app.utils.pack_description import normalize_pack_description
from app.utils.product_text import compose_product_query_text


def _product_nutrition_table() -> str:
    """Quoted schema.table for the PRIMARY product nutrition table."""
    return settings.reference_catalog_qualified_sql


def _score(a: str, b: str) -> float:
    na = normalize_pack_description(a)
    nb = normalize_pack_description(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio() * 100.0


def _to_nutrition(row: dict[str, Any]) -> NutritionData | None:
    """
    Convert a product_nutrition row to NutritionData.
    
    The new schema uses: sugar_g, fat_g, sodium_g (no separate trans_fat).
    """
    vals = {
        "total_fat": row.get("fat_g"),
        "total_sugar": row.get("sugar_g"),
        "sodium": row.get("sodium_g"),
    }
    if not any(v is not None for v in vals.values()):
        return None
    return NutritionData(
        total_fat=vals["total_fat"],
        trans_fat=None,
        total_sugar=vals["total_sugar"],
        sodium=vals["sodium"],
    )


def find_exact_reference_row(
    db: Session, product_info: ProductInfo | None
) -> dict[str, Any] | None:
    """
    Return the catalog row whose ``food_name`` matches ``product_info.name`` after
    the same normalization used for exact-name lookups (``normalize_pack_description``).

    Used by the catalog write path (``upsert_reference_product_from_ocr``). The key is
    ``product_info.name`` alone so the lookup key matches the INSERT key — otherwise
    repeat scans of the same product would insert duplicate rows.
    """
    if product_info is None:
        return None
    name = (product_info.name or "").strip()
    if not name:
        return None
    rows = _all_rows(db)
    if not rows:
        return None
    target_norm = normalize_pack_description(name)
    return next(
        (
            r
            for r in rows
            if normalize_pack_description(str(r.get("food_name") or ""))
            == target_norm
        ),
        None,
    )


def _all_rows(db: Session | None) -> list[dict[str, Any]]:
    """Fetch all rows from catalog.product_nutrition."""
    sql = text(
        f"""
        SELECT food_name, class_name, subclass_name, nova,
               sugar_g, fat_g, sodium_g, octagon_count
        FROM {_product_nutrition_table()}
        """
    )
    try:
        if db is not None:
            return [dict(r._mapping) for r in db.execute(sql).fetchall()]
        with engine.begin() as conn:
            return [dict(r._mapping) for r in conn.execute(sql).fetchall()]
    except SQLAlchemyError:
        return []


def lookup_reference_nutrition_db(
    product_info: ProductInfo | None,
    db: Session | None,
    min_score: float | None = None,
) -> tuple[NutritionData | None, ReferenceNutritionMatch | None]:
    """
    Look up nutrition data for a product, with two-table fallback:
    
    1. PRIMARY: ``catalog.product_nutrition`` (retail SKU match)
       - Try exact normalized name match
       - Try fuzzy name match (above ``min_score`` threshold)
       - If found: return the row's nutrition AS-IS (NULL nutrients stay NULL)
    
    2. SECONDARY: ``catalog.food_composition_reference``
       - Only consulted if primary completely misses (no exact, no fuzzy hit)
       - Try fuzzy name match against generic food names
       - If still no hit: skip (caller can use classification later for category lookup)
    
    ``min_score`` overrides ``settings.reference_catalog_fuzzy_min_score`` when set.
    """
    threshold = (
        float(settings.reference_catalog_fuzzy_min_score)
        if min_score is None
        else float(min_score)
    )
    if product_info is None:
        return None, None
    target = compose_product_query_text(product_info.name, product_info.brand)
    if not target:
        return None, None
    rows = _all_rows(db)
    target_norm = normalize_pack_description(target)
    
    # === PRIMARY LOOKUP: catalog.product_nutrition ===
    if rows:
        # Try exact name match first
        exact = next(
            (
                r
                for r in rows
                if normalize_pack_description(str(r.get("food_name") or "")) == target_norm
            ),
            None,
        )
        if exact is not None:
            nut = _to_nutrition(exact)
            if nut is not None:
                return nut, ReferenceNutritionMatch(
                    matched_product_name=str(exact.get("food_name") or target),
                    match_method="db_exact_name",
                    match_score=None,
                    sub_type=None,
                    form=None,
                )

        # Try fuzzy match
        best: dict[str, Any] | None = None
        best_score = 0.0
        for r in rows:
            s = _score(target, str(r.get("food_name") or ""))
            if s > best_score:
                best = r
                best_score = s
        if best is not None and best_score >= threshold:
            nut = _to_nutrition(best)
            if nut is not None:
                return nut, ReferenceNutritionMatch(
                    matched_product_name=str(best.get("food_name") or target),
                    match_method="db_fuzzy_name",
                    match_score=best_score,
                    sub_type=None,
                    form=None,
                )
    
    # === SECONDARY FALLBACK: catalog.food_composition_reference ===
    # Only reached when primary missed entirely (no exact, no fuzzy hit).
    sec_nut, sec_match = lookup_nutrition_in_food_composition_reference(
        target=target,
        threshold=threshold,
        db=db,
    )
    if sec_nut is not None and sec_match is not None:
        return sec_nut, sec_match

    return None, None


def lookup_product_classification_db(
    product_info: ProductInfo | None,
    db: Session | None,
    min_score: float | None = None,
) -> ProductClassification | None:
    """
    Look up product classification (class/subclass/nova) from the primary table.
    
    ``min_score`` overrides ``settings.reference_catalog_fuzzy_min_score`` when set.
    """
    threshold = (
        float(settings.reference_catalog_fuzzy_min_score)
        if min_score is None
        else float(min_score)
    )
    if product_info is None:
        return None
    target = compose_product_query_text(product_info.name, product_info.brand)
    if not target:
        return None
    rows = _all_rows(db)
    if not rows:
        return None
    target_norm = normalize_pack_description(target)
    exact = next(
        (
            r
            for r in rows
            if normalize_pack_description(str(r.get("food_name") or "")) == target_norm
        ),
        None,
    )
    if exact is not None:
        nv = exact.get("nova")
        return ProductClassification(
            class_name=exact.get("class_name"),
            subclass_name=exact.get("subclass_name"),
            nova=normalize_nova_for_api(
                str(nv).strip() if nv is not None and str(nv).strip() else None
            ),
            matched_description=exact.get("food_name"),
            match_method="db_exact_name",
            match_score=None,
        )
    best: dict[str, Any] | None = None
    best_score = 0.0
    for r in rows:
        s = _score(target, str(r.get("food_name") or ""))
        if s > best_score:
            best = r
            best_score = s
    if best is None or best_score < threshold:
        return None
    nv = best.get("nova")
    return ProductClassification(
        class_name=best.get("class_name"),
        subclass_name=best.get("subclass_name"),
        nova=normalize_nova_for_api(
            str(nv).strip() if nv is not None and str(nv).strip() else None
        ),
        matched_description=best.get("food_name"),
        match_method="db_fuzzy_name",
        match_score=best_score,
    )


def iter_reference_products_with_nutrition_db() -> list[tuple[str, NutritionData, dict[str, Any]]]:
    """
    Iterate all products with usable nutrition data for substitute recommendations.
    
    Returns: list of (product_name, NutritionData, raw_row_dict) tuples.
    """
    rows = _all_rows(None)
    out: list[tuple[str, NutritionData, dict[str, Any]]] = []
    for r in rows:
        pname = str(r.get("food_name") or "").strip()
        if not pname:
            continue
        nut = _to_nutrition(r)
        if nut is None:
            continue
        # Add backward-compatible aliases for callers that expect old field names
        r["product_name"] = pname
        r["sub_type"] = None
        r["form"] = None
        out.append((pname, nut, r))
    return out
