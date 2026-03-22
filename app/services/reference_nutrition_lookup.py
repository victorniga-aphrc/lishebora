"""
Match OCR product name to ``data/reference_nutrition_lookup.csv`` for per-100g facts.

Used when the vision model returns no usable numeric nutrition (missing or empty table).
Same normalization as POS / build script: ``normalize_pack_description``.
"""

from __future__ import annotations

import csv
import logging
from typing import Any

from rapidfuzz import fuzz, process

from app.config import settings
from app.models import NutritionData, ReferenceNutritionMatch
from app.utils.pos_description import normalize_pack_description

logger = logging.getLogger(__name__)


def _sku_like_scorer(query: str, choice: str, **kwargs: Any) -> float:
    return max(
        float(fuzz.WRatio(query, choice, **kwargs)),
        float(fuzz.partial_ratio(query, choice, **kwargs)),
    )


def _parse_float_cell(value: str | None) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        x = float(s)
        if x < 0:
            return None
        return x
    except ValueError:
        return None


def _row_to_nutrition(row: dict[str, str]) -> NutritionData | None:
    """Build NutritionData from CSV row; return None if there is no numeric data."""
    energy_kcal = _parse_float_cell(row.get("energy_kcal"))
    protein = _parse_float_cell(row.get("protein_g"))
    carbohydrates = _parse_float_cell(row.get("carbohydrates_g"))
    total_sugar = _parse_float_cell(row.get("total_sugar_g"))
    total_fat = _parse_float_cell(row.get("total_fat_g"))
    fiber = _parse_float_cell(row.get("fibre_g"))
    sodium = _parse_float_cell(row.get("sodium_g"))
    energy_kj = _parse_float_cell(row.get("energy_kj"))

    additional: dict[str, float] = {}
    if energy_kj is not None:
        additional["energy_kj"] = energy_kj

    # Need at least one macronutrient or kcal for KNPM / labelling (kJ alone is not enough)
    if not any(
        x is not None
        for x in (
            energy_kcal,
            protein,
            carbohydrates,
            total_sugar,
            total_fat,
            fiber,
            sodium,
        )
    ):
        return None

    return NutritionData(
        energy_kcal=energy_kcal,
        total_fat=total_fat,
        saturated_fat=None,
        trans_fat=None,
        total_sugar=total_sugar,
        sodium=sodium,
        protein=protein,
        carbohydrates=carbohydrates,
        fiber=fiber,
        additional_nutrients=additional,
    )


class _RefNutData:
    __slots__ = ("loaded", "names", "norm_to_row")

    def __init__(self) -> None:
        self.loaded = False
        self.names: list[str] = []
        self.norm_to_row: dict[str, dict[str, str]] = {}


_data = _RefNutData()


def _load_csv() -> None:
    if _data.loaded:
        return
    _data.loaded = True
    path = settings.reference_nutrition_lookup_csv
    if not path.exists():
        logger.warning(
            "Reference nutrition CSV not found at %s — reference fallback disabled.",
            path,
        )
        return
    try:
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("product_name") or "").strip()
                if not name:
                    continue
                n = normalize_pack_description(name)
                if not n:
                    continue
                # First row wins if duplicate keys after normalize
                if n not in _data.norm_to_row:
                    _data.norm_to_row[n] = {k: (v or "").strip() for k, v in row.items()}
                    _data.names.append(name)
    except OSError as e:
        logger.exception("Failed to load reference nutrition CSV: %s", e)


def lookup_reference_nutrition(
    product_info: Any | None,
) -> tuple[NutritionData | None, ReferenceNutritionMatch | None]:
    """
    Return (nutrition, match_meta) when a row has usable numbers and name matches.

    Exact match on normalized name first, then fuzzy against CSV ``product_name`` strings.
    """
    if not settings.reference_nutrition_lookup_enabled:
        return None, None

    _load_csv()
    if not _data.norm_to_row or not product_info:
        return None, None

    queries: list[tuple[str, str]] = []
    if product_info.name and str(product_info.name).strip():
        queries.append(("name", str(product_info.name).strip()))
    if (
        product_info.brand
        and product_info.name
        and str(product_info.brand).strip()
        and str(product_info.name).strip()
    ):
        combined = f"{str(product_info.brand).strip()} {str(product_info.name).strip()}".strip()
        if combined != str(product_info.name).strip():
            queries.append(("combined", combined))

    if not queries:
        return None, None

    min_score = float(settings.reference_nutrition_fuzzy_min_score)

    for source, q in queries:
        nq = normalize_pack_description(q)
        if nq in _data.norm_to_row:
            raw = _data.norm_to_row[nq]
            nut = _row_to_nutrition(raw)
            if nut is None:
                continue
            return nut, ReferenceNutritionMatch(
                matched_product_name=(raw.get("product_name") or nq).strip() or nq,
                match_method=f"exact_{source}",
                match_score=None,
                sub_type=(raw.get("sub_type") or "").strip() or None,
                form=(raw.get("form") or "").strip() or None,
            )

    best_name: str | None = None
    best_score = -1.0
    best_source = ""
    for source, q in queries:
        q_norm = normalize_pack_description(q)
        hit = process.extractOne(
            q_norm,
            _data.names,
            scorer=_sku_like_scorer,
            score_cutoff=min_score,
        )
        if hit is not None:
            match_s, score, _ = hit
            if float(score) > best_score:
                best_score = float(score)
                best_name = match_s
                best_source = source

    if best_name is None:
        return None, None

    nbest = normalize_pack_description(best_name)
    raw = _data.norm_to_row.get(nbest)
    if raw is None:
        return None, None

    nut = _row_to_nutrition(raw)
    if nut is None:
        return None, None

    return nut, ReferenceNutritionMatch(
        matched_product_name=(raw.get("product_name") or best_name).strip() or best_name,
        match_method=f"fuzzy_{best_source}",
        match_score=best_score,
        sub_type=(raw.get("sub_type") or "").strip() or None,
        form=(raw.get("form") or "").strip() or None,
    )
