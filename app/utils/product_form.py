"""
Infer whether a scanned pack is primarily **liquid**, **solid**, or **paste**
so substitute ranking can prefer the same ``form`` as in ``reference_nutrition_lookup.csv``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models import OcrResult


def canonical_food_form(value: str | None) -> str | None:
    """Normalize reference CSV / metadata strings to liquid | solid | paste."""
    if not value or not str(value).strip():
        return None
    v = str(value).strip().casefold()
    if v in ("liquid", "drink", "drinks", "beverage", "beverages"):
        return "liquid"
    if v in ("solid", "solids"):
        return "solid"
    if v in ("paste", "semi-solid", "semisolid", "semi solid"):
        return "paste"
    return None


_PER_100_ML = re.compile(
    r"(average\s+quantity\s+per\s+100\s*ml|per\s+100\s*ml\b|per\s+100ml\b)",
    re.IGNORECASE,
)


def infer_scan_form(ocr: OcrResult) -> str | None:
    """
    Best-effort form for the **scanned** product.

    Priority: reference row ``form`` → nutrition panel basis (100 ml) → POS + product text hints.
    """
    ref = ocr.reference_nutrition_match
    if ref is not None and ref.form:
        cf = canonical_food_form(ref.form)
        if cf is not None:
            return cf

    raw = ocr.raw_text or ""
    raw_l = raw.lower()
    if _PER_100_ML.search(raw_l):
        return "liquid"

    parts: list[str] = []
    if ocr.class_name:
        parts.append(ocr.class_name)
    if ocr.subclass_name:
        parts.append(ocr.subclass_name)
    sc = ocr.supermarket_classification
    if sc is not None and sc.matched_description:
        parts.append(sc.matched_description)
    if ocr.product_info is not None:
        pi = ocr.product_info
        for x in (pi.name, pi.category, pi.visual_product_type, pi.brand):
            if x:
                parts.append(x)
    blob = " ".join(parts).lower()

    liquid_markers = (
        "fruit drink",
        "soft drink",
        "energy drink",
        "juice",
        "nectar",
        "beverage",
        "soda",
        "mineral water",
        "bottled water",
        "spring water",
        "sparkling water",
        "carbonated",
        "tea drink",
        "coffee drink",
        "dairy drink",
        "milk drink",
        "yoghurt drink",
        "yogurt drink",
        "latte",
        "cappuccino",
        "smoothie",
        "cordial",
        "squash",
        "isotonic",
        "cola",
        "lemonade",
        "iced tea",
    )
    if any(m in blob for m in liquid_markers):
        return "liquid"
    if re.search(r"\b(mineral|spring|bottled|sparkling)?\s*water\b", blob):
        return "liquid"
    if re.search(r"\bmilk\b", blob) and "milk chocolate" not in blob and "milkshake" not in blob:
        return "liquid"

    paste_markers = (
        " jam",
        "spread",
        " sauce",
        "paste",
        "honey",
        "mayonnaise",
        " mayo",
        "chutney",
        " margarine",
    )
    if any(m in blob for m in paste_markers) and "drink" not in blob and "juice" not in blob:
        return "paste"

    solid_markers = (
        "crisp",
        "chip",
        "cracker",
        "bread",
        "rice",
        "flour",
        "cereal",
        "biscuit",
        "cookie",
        "snack bar",
        "chocolate bar",
        "nuts",
        "snack",
    )
    if any(m in blob for m in solid_markers):
        return "solid"

    return None


def form_sort_rank(scan_form: str | None, candidate_form_cell: str | None) -> int:
    """
    Lower is better when sorting substitutes.

    0 = same form as scan (or scan form unknown — no penalty).
    1 = candidate form unknown in catalog.
    2 = definite mismatch (e.g. solid vs liquid).
    """
    if not scan_form:
        return 0
    cc = canonical_food_form(candidate_form_cell)
    if cc == scan_form:
        return 0
    if cc is None:
        return 1
    return 2
