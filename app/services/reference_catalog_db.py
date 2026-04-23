"""PostgreSQL-backed reference catalog lookups for the active pipeline."""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine
from app.models import NutritionData, ProductInfo, ProductClassification, ReferenceNutritionMatch
from app.utils.nova_display import normalize_nova_for_api
from app.utils.pack_description import normalize_pack_description
from app.utils.product_text import compose_product_query_text


def _reference_products_from_clause() -> str:
    return settings.reference_catalog_qualified_sql


def _score(a: str, b: str) -> float:
    na = normalize_pack_description(a)
    nb = normalize_pack_description(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio() * 100.0


def _to_nutrition(row: dict[str, Any]) -> NutritionData | None:
    vals = {
        "total_fat": row.get("total_fat_g"),
        "total_sugar": row.get("total_sugar_g"),
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
    Return the catalog row whose ``product_name`` matches ``product_info.name`` after
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
            if normalize_pack_description(str(r.get("product_name") or ""))
            == target_norm
        ),
        None,
    )


def _all_rows(db: Session | None) -> list[dict[str, Any]]:
    sql = text(
        f"""
        SELECT product_name, class_name, subclass_name, nova,
               total_sugar_g, total_fat_g, sodium_g,
               sub_type, form, octagon_count
        FROM {_reference_products_from_clause()}
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
    ``min_score`` overrides ``settings.reference_catalog_fuzzy_min_score`` when set (e.g. tests).
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
    if not rows:
        return None, None
    target_norm = normalize_pack_description(target)
    exact = next(
        (
            r
            for r in rows
            if normalize_pack_description(str(r.get("product_name") or "")) == target_norm
        ),
        None,
    )
    if exact is not None:
        nut = _to_nutrition(exact)
        if nut is not None:
            return nut, ReferenceNutritionMatch(
                matched_product_name=str(exact.get("product_name") or target),
                match_method="db_exact_name",
                match_score=None,
                sub_type=(str(exact.get("sub_type")).strip() or None)
                if exact.get("sub_type") is not None
                else None,
                form=(str(exact.get("form")).strip() or None)
                if exact.get("form") is not None
                else None,
            )

    best: dict[str, Any] | None = None
    best_score = 0.0
    for r in rows:
        s = _score(target, str(r.get("product_name") or ""))
        if s > best_score:
            best = r
            best_score = s
    if best is None or best_score < threshold:
        return None, None
    nut = _to_nutrition(best)
    if nut is None:
        return None, None
    return nut, ReferenceNutritionMatch(
        matched_product_name=str(best.get("product_name") or target),
        match_method="db_fuzzy_name",
        match_score=best_score,
        sub_type=(str(best.get("sub_type")).strip() or None)
        if best.get("sub_type") is not None
        else None,
        form=(str(best.get("form")).strip() or None)
        if best.get("form") is not None
        else None,
    )


def lookup_product_classification_db(
    product_info: ProductInfo | None,
    db: Session | None,
    min_score: float | None = None,
) -> ProductClassification | None:
    """
    ``min_score`` overrides ``settings.reference_catalog_fuzzy_min_score`` when set (e.g. tests).
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
            if normalize_pack_description(str(r.get("product_name") or "")) == target_norm
        ),
        None,
    )
    if exact is not None:
        nv = exact.get("nova")
        return ProductClassification(
            class_name=exact.get("class_name"),
            subclass_name=exact.get("subclass_name"),
            nova=normalize_nova_for_api(str(nv).strip() if nv is not None and str(nv).strip() else None),
            matched_description=exact.get("product_name"),
            match_method="db_exact_name",
            match_score=None,
        )
    best: dict[str, Any] | None = None
    best_score = 0.0
    for r in rows:
        s = _score(target, str(r.get("product_name") or ""))
        if s > best_score:
            best = r
            best_score = s
    if best is None or best_score < threshold:
        return None
    nv = best.get("nova")
    return ProductClassification(
        class_name=best.get("class_name"),
        subclass_name=best.get("subclass_name"),
        nova=normalize_nova_for_api(str(nv).strip() if nv is not None and str(nv).strip() else None),
        matched_description=best.get("product_name"),
        match_method="db_fuzzy_name",
        match_score=best_score,
    )


def iter_reference_products_with_nutrition_db() -> list[tuple[str, NutritionData, dict[str, Any]]]:
    rows = _all_rows(None)
    out: list[tuple[str, NutritionData, dict[str, Any]]] = []
    for r in rows:
        pname = str(r.get("product_name") or "").strip()
        if not pname:
            continue
        nut = _to_nutrition(r)
        if nut is None:
            continue
        out.append((pname, nut, r))
    return out

