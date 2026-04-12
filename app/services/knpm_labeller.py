from __future__ import annotations

from typing import List

from app.config import settings
from app.models import KnpmLabel, NutritionData


def classify_with_knpm(
    nutrition: NutritionData | None,
    has_trans_fats: bool,
    has_sweeteners: bool,
) -> KnpmLabel:
    """
    KNPM-style classifier using per-100g/ml label (or reference) nutrition.

    Ingredient gates (trans fat, non-nutritive sweeteners) apply regardless.
    """
    octagons: List[str] = []
    reasons: List[str] = []

    fat_threshold = float(settings.knpm_fat_threshold)
    sugar_threshold = float(settings.knpm_sugar_threshold)
    sodium_threshold = float(settings.knpm_sodium_threshold)
    cat_label = "active KNPM thresholds"

    # 1. Ingredient gate: trans fats and artificial sweeteners
    if has_trans_fats:
        octagons.append("high_in_fat")
        reasons.append(
            "Trans fats detected in the ingredients list, which are discouraged by KNPM."
        )
    if has_sweeteners:
        octagons.append("high_in_sugar")
        reasons.append(
            "Non-nutritive/artificial sweeteners detected in the ingredients list."
        )

    # Ingredient gates classify as not healthy even when numeric nutrition is missing.
    if nutrition is None and octagons:
        return KnpmLabel(
            classification="not healthy",
            octagons=octagons,
            reasons=reasons,
            message=None,
        )

    # If we still have no nutrition data and no ingredient flags, classification is unknown.
    if nutrition is None:
        return KnpmLabel(
            classification="unknown",
            octagons=[],
            reasons=[],
            message=(
                "No usable nutrition data is available for KNPM numeric evaluation."
            ),
        )

    # 2. Nutrient thresholds (salt/sugar/fat only)
    if sugar_threshold is not None and nutrition.total_sugar is not None:
        if nutrition.total_sugar > sugar_threshold:
            if "high_in_sugar" not in octagons:
                octagons.append("high_in_sugar")
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
    if high_fat and "high_in_fat" not in octagons:
        octagons.append("high_in_fat")

    if sodium_threshold is not None and nutrition.sodium is not None:
        if nutrition.sodium > sodium_threshold:
            if "high_in_salt" not in octagons:
                octagons.append("high_in_salt")
            reasons.append(
                f"Sodium {nutrition.sodium:.2f} g/100g exceeds KNPM limit for "
                f"{cat_label}: {sodium_threshold} g/100g."
            )

    # Pipeline requirement: at least one of salt/sugar/fat must be available.
    if (
        nutrition.total_sugar is None
        and nutrition.total_fat is None
        and nutrition.sodium is None
    ):
        if octagons:
            return KnpmLabel(
                classification="not healthy",
                octagons=octagons,
                reasons=reasons,
                message=None,
            )
        return KnpmLabel(
            classification="unknown",
            octagons=[],
            reasons=[],
            message=(
                "No usable nutrition data is available for KNPM numeric evaluation."
            ),
        )

    if not octagons:
        return KnpmLabel(
            classification="healthy",
            octagons=[],
            reasons=[
                f"All assessed nutrients of concern are within KNPM limits for {cat_label}."
            ],
            message=None,
        )

    return KnpmLabel(
        classification="not healthy",
        octagons=octagons,
        reasons=reasons,
        message=None,
    )
