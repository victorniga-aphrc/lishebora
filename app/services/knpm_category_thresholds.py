"""
Load official KNPM per-category nutrient thresholds (g per 100 g/ml).

Source: ``data/knpm_category_threshold.csv`` — these are **regulatory limits** for
when a product is "high in" fat / saturated fat / sugars / sodium for that food
category. They are **not** typical nutrient compositions and cannot replace
product-level reference nutrition.

Resolution (in order):

1. Fuzzy-match **several hint variants** (POS-only, OCR-only, combined) against
   ``category_name`` using **max(token_set_ratio, partial_ratio, WRatio)** so
   short retail labels like "WHITE BREAD" still align with long KNPM names like
   "Breads and ordinary bakery products".

2. **POS class bridge** — when fuzzy still fails, map known retailer ``class_name``
   values (e.g. ``BREADS``) to an official KNPM category number.

3. Fall back to **6.0 Composite foods** when the CSV is loaded but nothing matched.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from rapidfuzz import fuzz, process

from app.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KnpmThresholdRow:
    """One row from the KNPM category threshold table."""

    category_number: str
    category_name: str
    total_fat_g: float | None
    saturated_fat_g: float | None
    total_sugar_g: float | None
    sodium_g: float | None


class _Loaded:
    __slots__ = ("loaded", "rows", "names")

    def __init__(self) -> None:
        self.loaded = False
        self.rows: list[KnpmThresholdRow] = []
        self.names: list[str] = []


_state = _Loaded()

# Retail POS ``class_name`` (uppercase) → KNPM ``category_number`` when fuzzy fails.
# Extend carefully; wrong mappings are worse than composite 6.0.
_POS_CLASS_TO_KNPM_NUMBER: dict[str, str] = {
    "BREADS": "2.2",
    "BREAD": "2.2",
}


def _knpm_category_scorer(query: str, choice: str, **kwargs: Any) -> float:
    """Stronger than token_set alone: short POS lines vs long MoH category names."""
    return max(
        float(fuzz.token_set_ratio(query, choice, **kwargs)),
        float(fuzz.partial_ratio(query, choice, **kwargs)),
        float(fuzz.WRatio(query, choice, **kwargs)),
    )


def _parse_cell(value: str | None) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() == "null":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _load_csv(path: Path) -> None:
    if _state.loaded:
        return
    _state.loaded = True
    if not path.exists():
        logger.warning(
            "KNPM category threshold CSV not found at %s — using legacy fixed thresholds.",
            path,
        )
        return
    try:
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                num = (row.get("category_number") or "").strip()
                name = (row.get("category_name") or "").strip()
                if not num or not name:
                    continue
                r = KnpmThresholdRow(
                    category_number=num,
                    category_name=name,
                    total_fat_g=_parse_cell(row.get("total_fat_g_per_100g_ml")),
                    saturated_fat_g=_parse_cell(
                        row.get("saturated_fat_g_per_100g_ml")
                    ),
                    total_sugar_g=_parse_cell(row.get("total_sugar_g_per_100g_ml")),
                    sodium_g=_parse_cell(row.get("sodium_g_per_100g_ml")),
                )
                _state.rows.append(r)
                _state.names.append(name)
    except OSError as e:
        logger.exception("Failed to load KNPM category thresholds: %s", e)


def _row_by_category_number(num: str) -> KnpmThresholdRow | None:
    for r in _state.rows:
        if r.category_number == num:
            return r
    return None


def _composite_default_row() -> KnpmThresholdRow | None:
    return _row_by_category_number("6.0")


def load_knpm_threshold_rows() -> list[KnpmThresholdRow]:
    """Force-load CSV (for tests)."""
    _load_csv(settings.knpm_category_threshold_csv)
    return list(_state.rows)


def build_knpm_hint_from_context(
    product_info: Any | None,
    supermarket_classification: Any | None,
) -> str:
    """Single string of OCR + POS text (legacy / debugging)."""
    parts: list[str] = []
    if product_info is not None:
        for attr in ("category", "visual_product_type", "name"):
            v = getattr(product_info, attr, None)
            if v and str(v).strip():
                parts.append(str(v).strip())
    if supermarket_classification is not None:
        for attr in ("subclass_name", "class_name", "matched_description"):
            v = getattr(supermarket_classification, attr, None)
            if v and str(v).strip():
                parts.append(str(v).strip())
    return " ".join(parts)


def _build_knpm_hint_variants(
    product_info: Any | None,
    supermarket_classification: Any | None,
) -> list[str]:
    """
    Multiple query strings; try all and keep the best fuzzy score.

    Order: POS-focused first (closest to KNPM food-type phrases), then OCR-only,
    then the legacy all-in-one string.
    """
    seen_keys: set[str] = set()
    out: list[str] = []

    def add(s: str) -> None:
        t = s.strip()
        if len(t) < 3:
            return
        k = t.casefold()
        if k in seen_keys:
            return
        seen_keys.add(k)
        out.append(t)

    if supermarket_classification is not None:
        pos_parts: list[str] = []
        for attr in ("subclass_name", "class_name", "matched_description"):
            v = getattr(supermarket_classification, attr, None)
            if v and str(v).strip():
                pos_parts.append(str(v).strip())
        if pos_parts:
            add(" ".join(pos_parts))

    if product_info is not None:
        ocr_parts: list[str] = []
        for attr in ("visual_product_type", "name", "brand", "category"):
            v = getattr(product_info, attr, None)
            if v and str(v).strip():
                ocr_parts.append(str(v).strip())
        if ocr_parts:
            add(" ".join(ocr_parts))

    add(build_knpm_hint_from_context(product_info, supermarket_classification))

    return out


def _bridge_pos_class_to_knpm_row(supermarket_classification: Any | None) -> KnpmThresholdRow | None:
    """Map retailer class_name to a KNPM row when fuzzy match fails."""
    if supermarket_classification is None:
        return None
    sub_u = (getattr(supermarket_classification, "subclass_name", None) or "").upper()
    # Avoid mapping crumb / biscuit lines to "bread" limits
    if "CRUMB" in sub_u or "BREADCRUMB" in sub_u.replace(" ", ""):
        return None
    if "SHORTBREAD" in sub_u:
        return None

    cls = getattr(supermarket_classification, "class_name", None)
    if not cls or not str(cls).strip():
        return None
    cls_u = str(cls).strip().upper()
    num = _POS_CLASS_TO_KNPM_NUMBER.get(cls_u)
    if num is None:
        return None
    return _row_by_category_number(num)


ThresholdSource = Literal[
    "csv_fuzzy",
    "csv_pos_class_bridge",
    "csv_default_composite",
    "hardcoded_fallback",
]


def _hints_blob_lower(variants: list[str]) -> str:
    return " ".join(v.lower() for v in variants if v and str(v).strip())


def _prefer_fruit_veg_drink_over_rtd_tea_coffee_mismatch(
    chosen: KnpmThresholdRow,
    variants: list[str],
) -> KnpmThresholdRow:
    """
    Fuzzy KNPM match sometimes lands on **5.3** (coffee / tea / cocoa RTD) for retail
    juice / fruit-drink lines (POS subclass often contains ``JUICE``).

    When hints clearly describe juice / fruit drink and not coffee or cocoa, use **5.1**
    (Fruit and vegetable drinks) if present in the CSV.
    """
    if chosen.category_number != "5.3":
        return chosen
    blob = _hints_blob_lower(variants)
    juice_like = (
        "juice" in blob
        or "fruit drink" in blob
        or "nectar" in blob
        or "smoothie" in blob
    )
    if not juice_like:
        return chosen
    if "coffee" in blob or "cocoa" in blob:
        return chosen
    # Plain "tea" without juice can be RTD tea (5.3); keep 5.3 unless fruit/juice cues exist
    if "tea" in blob and "juice" not in blob and "fruit drink" not in blob:
        return chosen
    alt = _row_by_category_number("5.1")
    return alt if alt is not None else chosen


def resolve_knpm_thresholds_for_extraction(
    product_info: Any | None,
    supermarket_classification: Any | None,
) -> tuple[KnpmThresholdRow | None, ThresholdSource, float | None]:
    """
    Resolve KNPM threshold row for one scan.

    Returns ``(row, source, fuzzy_score)``. ``fuzzy_score`` is set only for
    ``csv_fuzzy``.
    """
    _load_csv(settings.knpm_category_threshold_csv)
    if not _state.rows:
        return None, "hardcoded_fallback", None

    min_score = float(settings.knpm_category_fuzzy_min_score)
    variants = _build_knpm_hint_variants(product_info, supermarket_classification)

    best_row: KnpmThresholdRow | None = None
    best_score = -1.0
    for variant in variants:
        hit = process.extractOne(
            variant,
            _state.names,
            scorer=_knpm_category_scorer,
            score_cutoff=min_score,
        )
        if hit is not None:
            _m, score, idx = hit
            sc = float(score)
            if sc > best_score:
                best_score = sc
                best_row = _state.rows[idx]

    if best_row is not None:
        best_row = _prefer_fruit_veg_drink_over_rtd_tea_coffee_mismatch(
            best_row, variants
        )
        return best_row, "csv_fuzzy", best_score

    bridged = _bridge_pos_class_to_knpm_row(supermarket_classification)
    if bridged is not None:
        return bridged, "csv_pos_class_bridge", None

    comp = _composite_default_row()
    if comp is not None:
        return comp, "csv_default_composite", None

    return None, "hardcoded_fallback", None


def resolve_knpm_threshold_row(
    hint: str | None,
    supermarket_classification: Any | None = None,
) -> tuple[KnpmThresholdRow | None, ThresholdSource, float | None]:
    """
    Resolve from a pre-built hint string (optionally with POS for class bridge).

    Prefer :func:`resolve_knpm_thresholds_for_extraction` from the OCR pipeline.
    """
    _load_csv(settings.knpm_category_threshold_csv)
    if not _state.rows:
        return None, "hardcoded_fallback", None

    min_score = float(settings.knpm_category_fuzzy_min_score)
    best_row: KnpmThresholdRow | None = None
    best_score = -1.0
    h = (hint or "").strip()
    if len(h) >= 3:
        hit = process.extractOne(
            h,
            _state.names,
            scorer=_knpm_category_scorer,
            score_cutoff=min_score,
        )
        if hit is not None:
            _m, score, idx = hit
            best_row = _state.rows[idx]
            best_score = float(score)

    if best_row is not None:
        return best_row, "csv_fuzzy", best_score

    bridged = _bridge_pos_class_to_knpm_row(supermarket_classification)
    if bridged is not None:
        return bridged, "csv_pos_class_bridge", None

    comp = _composite_default_row()
    if comp is not None:
        return comp, "csv_default_composite", None

    return None, "hardcoded_fallback", None
