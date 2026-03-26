"""Tests for scan form inference (liquid vs solid) used by healthier substitutes."""

from __future__ import annotations

from app.models import (
    ExtractionMetadata,
    KnpmLabel,
    OcrResult,
    ProductInfo,
    SupermarketClassification,
)
from app.utils.product_form import canonical_food_form, form_sort_rank, infer_scan_form


def test_infer_liquid_from_per_100_ml_on_label() -> None:
    ocr = OcrResult(
        ingredients=[],
        nutrition_per_100g=None,
        product_info=ProductInfo(name="Orchid Valley Delight", category="fruit drink"),
        raw_text=(
            "Average Quantity per 100 ml\nEnergy 284 kJ\n"
            "- sugars 15.9 g\n"
        ),
        extraction_metadata=ExtractionMetadata(),
        knpm_label=KnpmLabel(classification="LESS_HEALTHY", octagons=["HIGH_IN_SUGAR"], reasons=[]),
        supermarket_classification=SupermarketClassification(
            class_name="FRESH/FRUIT JUICES - NO ADDED SUGAR",
            subclass_name="FRESH/FRUIT JUICES - NO ADDED SUGAR",
            matched_description="ORCHID VALLEY DELIGHT APPLE",
            match_method="fuzzy_name",
            match_score=100.0,
        ),
    )
    assert infer_scan_form(ocr) == "liquid"


def test_form_sort_rank_prefers_liquid_for_liquid_scan() -> None:
    assert form_sort_rank("liquid", "Liquid") == 0
    assert form_sort_rank("liquid", "Solid") == 2
    assert form_sort_rank("liquid", None) == 1
    assert form_sort_rank(None, "Solid") == 0


def test_canonical_food_form() -> None:
    assert canonical_food_form("Liquid") == "liquid"
    assert canonical_food_form("SOLID") == "solid"
