from __future__ import annotations

from typing import TYPE_CHECKING, List, Literal

from app.models import KnpmLabel, NutritionData

if TYPE_CHECKING:
    from app.services.knpm_category_thresholds import KnpmThresholdRow

# When ``knpm_category_threshold.csv`` is missing or unusable
_LEGACY_FAT = 7.76
_LEGACY_SAT_FAT = 6.33
_LEGACY_SUGAR = 4.7
_LEGACY_SODIUM = 0.26


def classify_with_knpm(
    nutrition: NutritionData | None,
    has_trans_fats: bool,
    has_sweeteners: bool,
    *,
    threshold_row: "KnpmThresholdRow | None" = None,
    thresholds_source: Literal[
        "csv_fuzzy",
        "csv_pos_class_bridge",
        "csv_default_composite",
        "hardcoded_fallback",
    ]
    | None = None,
    category_match_score: float | None = None,
) -> KnpmLabel:
    """
    KNPM-style classifier using per-100g/ml label (or reference) nutrition.

    When ``threshold_row`` is set, nutrient limits come from the official KNPM
    category table (``data/knpm_category_threshold.csv``). Otherwise legacy
    fixed thresholds are used (same numeric defaults as before the CSV existed).

    Ingredient gates (trans fat, non-nutritive sweeteners) apply regardless.
    """
    meta_kwargs = {
        "knpm_category_number": None,
        "knpm_category_name": None,
        "knpm_category_match_score": None,
        "knpm_thresholds_source": thresholds_source,
    }

    if threshold_row is not None:
        meta_kwargs["knpm_category_number"] = threshold_row.category_number
        meta_kwargs["knpm_category_name"] = threshold_row.category_name
        if thresholds_source == "csv_fuzzy":
            meta_kwargs["knpm_category_match_score"] = category_match_score

    # If we have no nutrition data, we cannot apply KNPM numeric thresholds.
    if nutrition is None:
        return KnpmLabel(
            classification="UNKNOWN",
            octagons=[],
            reasons=[],
            message=(
                "Nutrition facts table not found on the label. "
                "KNPM-based classification cannot be applied."
            ),
            **meta_kwargs,
        )

    octagons: List[str] = []
    reasons: List[str] = []

    if threshold_row is not None:
        fat_threshold = threshold_row.total_fat_g
        sat_fat_threshold = threshold_row.saturated_fat_g
        sugar_threshold = threshold_row.total_sugar_g
        sodium_threshold = threshold_row.sodium_g
        cat_label = (
            f"{threshold_row.category_name} (KNPM category {threshold_row.category_number})"
        )
    else:
        fat_threshold = _LEGACY_FAT
        sat_fat_threshold = _LEGACY_SAT_FAT
        sugar_threshold = _LEGACY_SUGAR
        sodium_threshold = _LEGACY_SODIUM
        cat_label = "general reference thresholds (legacy)"
        meta_kwargs["knpm_thresholds_source"] = "hardcoded_fallback"

    # 1. Ingredient gate: trans fats and artificial sweeteners
    if has_trans_fats:
        octagons.append("HIGH_IN_FAT")
        reasons.append(
            "Trans fats detected in the ingredients list, which are discouraged by KNPM."
        )
    if has_sweeteners:
        octagons.append("HIGH_IN_SUGAR")
        reasons.append(
            "Non-nutritive/artificial sweeteners detected in the ingredients list."
        )

    # 2. Nutrient thresholds (per category when available)
    if sugar_threshold is not None and nutrition.total_sugar is not None:
        if nutrition.total_sugar > sugar_threshold:
            if "HIGH_IN_SUGAR" not in octagons:
                octagons.append("HIGH_IN_SUGAR")
            reasons.append(
                f"Total sugar {nutrition.total_sugar:.2f} g/100g exceeds KNPM limit for "
                f"{cat_label}: {sugar_threshold} g/100g."
            )

    high_fat = False
    if fat_threshold is not None and nutrition.total_fat is not None:
        if nutrition.total_fat > fat_threshold:
            high_fat = True
            reasons.append(
                f"Total fat {nutrition.total_fat:.2f} g/100g exceeds KNPM limit for "
                f"{cat_label}: {fat_threshold} g/100g."
            )
    if sat_fat_threshold is not None and nutrition.saturated_fat is not None:
        if nutrition.saturated_fat > sat_fat_threshold:
            high_fat = True
            reasons.append(
                f"Saturated fat {nutrition.saturated_fat:.2f} g/100g exceeds KNPM limit for "
                f"{cat_label}: {sat_fat_threshold} g/100g."
            )
    if high_fat and "HIGH_IN_FAT" not in octagons:
        octagons.append("HIGH_IN_FAT")

    if sodium_threshold is not None and nutrition.sodium is not None:
        if nutrition.sodium > sodium_threshold:
            if "HIGH_IN_SALT" not in octagons:
                octagons.append("HIGH_IN_SALT")
            reasons.append(
                f"Sodium {nutrition.sodium:.2f} g/100g exceeds KNPM limit for "
                f"{cat_label}: {sodium_threshold} g/100g."
            )

    # If ALL numeric nutrition info is missing (no values at all), treat as unknown.
    if (
        nutrition.energy_kcal is None
        and nutrition.total_sugar is None
        and nutrition.total_fat is None
        and nutrition.saturated_fat is None
        and nutrition.trans_fat is None
        and nutrition.sodium is None
        and nutrition.protein is None
        and nutrition.carbohydrates is None
        and nutrition.fiber is None
        and not nutrition.additional_nutrients
    ):
        return KnpmLabel(
            classification="UNKNOWN",
            octagons=[],
            reasons=[],
            message=(
                "No numeric nutrition information available on the label. "
                "KNPM-based classification cannot be applied."
            ),
            **meta_kwargs,
        )

    if not octagons:
        return KnpmLabel(
            classification="FIT_FOR_CONSUMPTION",
            octagons=[],
            reasons=[
                f"All assessed nutrients of concern are within KNPM limits for {cat_label}."
            ],
            message=None,
            **meta_kwargs,
        )

    return KnpmLabel(
        classification="LESS_HEALTHY",
        octagons=octagons,
        reasons=reasons,
        message=None,
        **meta_kwargs,
    )
