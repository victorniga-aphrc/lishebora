"""
Practical substitute relevance beyond **form** (liquid/solid).

Example: olive oil is *liquid* but not a sensible alternative to a **fruit juice /
soft drink**. We rank pantry oils, vinegars, and similar **after** drink-like products
when the scan clearly looks like a beverage.
"""

from __future__ import annotations

import re
from app.models import OcrResult


def _scan_context_blob(ocr: OcrResult) -> str:
    parts: list[str] = []
    if ocr.class_name:
        parts.append(ocr.class_name)
    if ocr.subclass_name:
        parts.append(ocr.subclass_name)
    sc = ocr.supermarket_classification
    if sc is not None:
        for x in (sc.matched_description, sc.class_name, sc.subclass_name):
            if x:
                parts.append(x)
    if ocr.product_info is not None:
        pi = ocr.product_info
        for x in (pi.name, pi.category, pi.visual_product_type, pi.brand):
            if x:
                parts.append(x)
    return " ".join(parts).lower()


_BEVERAGE_MARKERS = (
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
    "iced tea",
    "tea drink",
    "coffee drink",
    "milk drink",
    "yoghurt drink",
    "yogurt drink",
    "smoothie",
    "cordial",
    "squash",
    "isotonic",
    "cola",
    "lemonade",
)


def infer_beverage_like_liquid_scan(ocr: OcrResult, scan_form: str | None) -> bool:
    """
    True when the scan is **liquid** and context suggests a **drink** (not e.g. cooking oil).
    """
    if scan_form != "liquid":
        return False
    blob = _scan_context_blob(ocr)
    if any(m in blob for m in _BEVERAGE_MARKERS):
        return True
    if re.search(r"\b(milk|water)\b", blob) and "milk chocolate" not in blob:
        return True
    raw = (ocr.raw_text or "").lower()
    if re.search(r"average\s+quantity\s+per\s+100\s*ml", raw) and any(
        m in raw for m in ("sugar", "sugars", "carbohydrate", "juice", "drink")
    ):
        return True
    return False


_PANTRY_LIQUID_NAME_MARKERS = (
    "olive oil",
    "sunflower oil",
    "vegetable oil",
    "cooking oil",
    "sesame oil",
    "canola oil",
    "corn oil",
    "palm oil",
    "coconut oil",
    "fish oil",
    "cod liver",
    "extra virgin",
    "vinegar",
    "soy sauce",
    "worcestershire",
    "truffle oil",
    "salad oil",
)

_BEVERAGE_NAME_HINTS = (
    " juice",
    " drink",
    " milk",
    " tea",
    " coffee",
    " water",
    " soda",
    " cola",
    " nectar",
    "smoothie",
    " isotonic",
    " lemonade",
    "cordial",
    " squash",
    " kvass",
    " kombucha",
)


def _sub_type_tokens(st: str) -> set[str]:
    return {t for t in re.split(r"[/,\s]+", st.casefold()) if len(t) > 1}


def liquid_beverage_practicality_rank(ocr: OcrResult, scan_form: str | None, cand: object) -> int:
    """
    Only matters when :func:`infer_beverage_like_liquid_scan` is True.

    Return **lower** for more practical drink substitutes:

    - 0 — likely intended beverage (juice, soft drink, milk, water, etc.)
    - 1 — ambiguous liquid (unknown role)
    - 2 — likely pantry / cooking / condiment oil or vinegar
    """
    if not infer_beverage_like_liquid_scan(ocr, scan_form):
        return 0

    name = str(getattr(cand, "product_name", "") or "").casefold()
    st_raw = str(getattr(cand, "sub_type", "") or "").strip().casefold()

    for m in _PANTRY_LIQUID_NAME_MARKERS:
        if m in name:
            return 2

    if re.search(r"\boil\b", name) or name.rstrip().endswith(" oil"):
        return 2

    if "vinegar" in name:
        return 2

    if st_raw:
        tokens = _sub_type_tokens(st_raw)
        if "oil" in tokens or "vinegar" in tokens or "fat" in tokens:
            if "drink" not in st_raw and "juice" not in st_raw:
                return 2
        if any(
            t in tokens
            for t in (
                "drink",
                "juice",
                "beverage",
                "milk",
                "water",
                "tea",
                "coffee",
                "soda",
                "nectar",
                "cordial",
                "squash",
            )
        ):
            return 0

    for h in _BEVERAGE_NAME_HINTS:
        if h in name:
            return 0

    if re.match(r"^milk\b", name) or re.search(r"\bmilk$", name):
        return 0
    if re.search(r"\b(mineral|spring|sparkling|bottled)\s+water\b", name):
        return 0

    return 1


def is_probable_pantry_liquid_substitute(product_name: str, sub_type: str | None) -> bool:
    """True if name/sub_type look like oil or vinegar rather than a drink."""
    name = product_name.casefold()
    st_raw = (sub_type or "").strip().casefold()
    for m in _PANTRY_LIQUID_NAME_MARKERS:
        if m in name:
            return True
    if re.search(r"\boil\b", name) or name.rstrip().endswith(" oil"):
        return True
    if "vinegar" in name:
        return True
    if st_raw:
        tokens = _sub_type_tokens(st_raw)
        if ("oil" in tokens or "vinegar" in tokens) and "drink" not in st_raw and "juice" not in st_raw:
            return True
    return False
