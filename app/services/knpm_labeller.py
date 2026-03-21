from __future__ import annotations

from typing import List

from app.models import KnpmLabel, NutritionData


def classify_with_knpm(
    nutrition: NutritionData | None,
    has_trans_fats: bool,
    has_sweeteners: bool,
) -> KnpmLabel:
    """
    Very first KNPM-based classifier for demo purposes.

    - Assumes per-100g / per-100ml values in `nutrition`.
    - Uses a single set of thresholds (aligned with KNPM snack thresholds)
      for total sugar, total fat, saturated fat, and sodium.
    - Returns:
      - overall classification (FIT_FOR_CONSUMPTION / LESS_HEALTHY / UNKNOWN)
      - list of specific octagon warnings: HIGH_IN_SUGAR, HIGH_IN_SALT, HIGH_IN_FAT
      - reasons explaining the decision.

    NOTE: This is a simplified implementation for the demo. In the future we
    will:
      - load per-category thresholds from KNPM tables,
      - vary thresholds by KNPM category (1–11),
      - extend octagon types if MoH adds more.
    """
    # If we have no nutrition data, we cannot apply KNPM thresholds.
    if nutrition is None:
        return KnpmLabel(
            classification="UNKNOWN",
            octagons=[],
            reasons=[],
            message=(
                "Nutrition facts table not found on the label. "
                "KNPM-based classification cannot be applied."
            ),
        )

    octagons: List[str] = []
    reasons: List[str] = []

    # 1. Ingredient gate: trans fats and artificial sweeteners
    if has_trans_fats:
        octagons.append("HIGH_IN_FAT")
        reasons.append(
            "Trans fats detected in the ingredients list, which are discouraged by KNPM."
        )
    if has_sweeteners:
        # In the KNPM logic, products with artificial sweeteners are treated as less healthy.
        octagons.append("HIGH_IN_SUGAR")
        reasons.append(
            "Non-nutritive/artificial sweeteners detected in the ingredients list."
        )

    # 2. Nutrient thresholds (simplified, snack-like defaults from idea.md / WORKFLOW.md)
    # These are used for now for any product where nutrition is available.
    sugar_threshold = 4.7   # g per 100g
    fat_threshold = 7.76    # g per 100g
    sat_fat_threshold = 6.33  # g per 100g
    sodium_threshold = 0.26   # g per 100g

    # Sugar
    if nutrition.total_sugar is not None and nutrition.total_sugar > sugar_threshold:
        if "HIGH_IN_SUGAR" not in octagons:
            octagons.append("HIGH_IN_SUGAR")
        reasons.append(
            f"Total sugar {nutrition.total_sugar:.2f} g/100g exceeds KNPM threshold {sugar_threshold:.2f} g/100g."
        )

    # Fat / saturated fat
    high_fat = False
    if nutrition.total_fat is not None and nutrition.total_fat > fat_threshold:
        high_fat = True
        reasons.append(
            f"Total fat {nutrition.total_fat:.2f} g/100g exceeds KNPM threshold {fat_threshold:.2f} g/100g."
        )
    if nutrition.saturated_fat is not None and nutrition.saturated_fat > sat_fat_threshold:
        high_fat = True
        reasons.append(
            f"Saturated fat {nutrition.saturated_fat:.2f} g/100g exceeds KNPM threshold {sat_fat_threshold:.2f} g/100g."
        )
    if high_fat and "HIGH_IN_FAT" not in octagons:
        octagons.append("HIGH_IN_FAT")

    # Sodium (salt)
    if nutrition.sodium is not None and nutrition.sodium > sodium_threshold:
        if "HIGH_IN_SALT" not in octagons:
            octagons.append("HIGH_IN_SALT")
        reasons.append(
            f"Sodium {nutrition.sodium:.2f} g/100g exceeds KNPM threshold {sodium_threshold:.2f} g/100g."
        )

    # If ALL numeric nutrition info is missing (no values at all), treat as unknown.
    # We check core KNPM nutrients plus other macronutrients and additional_nutrients.
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
        )

    if not octagons:
        # All good according to the thresholds and ingredient flags
        return KnpmLabel(
            classification="FIT_FOR_CONSUMPTION",
            octagons=[],
            reasons=["All nutrients of concern are within KNPM thresholds."],
            message=None,
        )

    # One or more warnings → overall classification is less healthy
    return KnpmLabel(
        classification="LESS_HEALTHY",
        octagons=octagons,
        reasons=reasons,
        message=None,
    )

