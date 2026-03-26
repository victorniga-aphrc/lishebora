"""Beverage vs pantry-liquid ranking for substitutes."""

from __future__ import annotations

from app.models import (
    ExtractionMetadata,
    KnpmLabel,
    NutritionData,
    OcrResult,
    ProductInfo,
    SupermarketClassification,
)
from app.services.healthier_substitutes import _Cand
from app.utils.product_form import infer_scan_form
from app.utils.substitute_practicality import (
    infer_beverage_like_liquid_scan,
    is_probable_pantry_liquid_substitute,
    liquid_beverage_practicality_rank,
)


def _fruit_drink_ocr() -> OcrResult:
    return OcrResult(
        ingredients=[],
        nutrition_per_100g=NutritionData(total_sugar=15.9, sodium=0.0, total_fat=0.0),
        product_info=ProductInfo(
            name="Orchid Valley Delight",
            brand="Orchid Valley",
            category="fruit drink",
            visual_product_type="fruit drink",
        ),
        raw_text="Average Quantity per 100 ml\nSugars 15.9 g\n",
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


def test_beverage_context_for_fruit_drink() -> None:
    ocr = _fruit_drink_ocr()
    sf = infer_scan_form(ocr)
    assert sf == "liquid"
    assert infer_beverage_like_liquid_scan(ocr, sf) is True


def test_oil_ranks_worse_than_juice() -> None:
    ocr = _fruit_drink_ocr()
    sf = infer_scan_form(ocr)
    juice = _Cand(
        product_name="AFIA APPLE JUICE",
        nutrition=NutritionData(),
        class_name=None,
        subclass_name=None,
        sub_type="Drink",
        form="Liquid",
        octagons=[],
        below=True,
    )
    oil = _Cand(
        product_name="AL JAZIRA EXTRA VIRGIN OLIVE OIL",
        nutrition=NutritionData(),
        class_name=None,
        subclass_name=None,
        sub_type=None,
        form="Liquid",
        octagons=[],
        below=True,
    )
    assert liquid_beverage_practicality_rank(ocr, sf, juice) < liquid_beverage_practicality_rank(ocr, sf, oil)


def test_is_probable_pantry_liquid() -> None:
    assert is_probable_pantry_liquid_substitute("EXTRA VIRGIN OLIVE OIL", None) is True
    assert is_probable_pantry_liquid_substitute("AFIA APPLE DRINK", "Drink") is False
