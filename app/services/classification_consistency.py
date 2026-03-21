"""
Detect contradictions between retail POS taxonomy and label-based KNPM signals.

POS class/subclass comes from historical transaction data (SKU descriptions).
KNPM uses nutrients read from *this* pack. They can disagree if the SKU match is
wrong, formulation changed, or the retail category was always approximate.
"""

from __future__ import annotations

from app.models import KnpmLabel, SupermarketClassification


def warning_pos_taxonomy_vs_label_sugar(
    knpm_label: KnpmLabel | None,
    supermarket_classification: SupermarketClassification | None,
) -> str | None:
    """
    If POS taxonomy implies “no added sugar” but KNPM flags high sugar from the label,
    return a warning for the API consumer. Otherwise None.
    """
    if supermarket_classification is None or knpm_label is None:
        return None

    bucket = " ".join(
        [
            supermarket_classification.class_name or "",
            supermarket_classification.subclass_name or "",
        ]
    ).upper()

    # Phrases in this dataset’s POS taxonomy that imply low/no added sugar positioning
    sugar_claim_tokens = (
        "NO ADDED SUGAR",
        "NO ADDED SUGARS",
        "UNSWEETENED",
        "WITHOUT ADDED SUGAR",
    )
    if not any(t in bucket for t in sugar_claim_tokens):
        return None

    octagons = knpm_label.octagons or []
    if "HIGH_IN_SUGAR" not in octagons:
        return None

    return (
        "Retail POS taxonomy suggests a no/low added-sugar category, but this label’s "
        "sugar level triggers KNPM high sugar. Trust the nutrition panel for health "
        "assessment; the POS class may be wrong, outdated, or from a different variant "
        "than the matched SKU line."
    )
