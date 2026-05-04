"""
Healthier product substitutes: tiered PostgreSQL catalog search + KNPM ranking.

Tiers (based on taxonomy fields already stored in
``catalog.product_nutrition``):

- **Tier 1**: same ``subclass_name`` as the scan.
- **Tier 2**: same ``class_name``, different subclass (or Tier 1 empty).
- **Tier 3**: full reference pool (still scored with the **scan's** KNPM category limits).

Only catalog rows with **zero** KNPM octagons are eligible as substitutes; rows with any
octagon are excluded. Ingredient-only flags are **not** applied to catalog rows (no
ingredient list in the reference table).

**Fill order:** take up to ``max`` substitutes from tier 1 (same subclass). If fewer than
``min``, add from tier 2 (same class, excluding tier‑1 pool). If still short, add from
tier 3 (rest of catalog). No skipping tier 1 when subclass matches are “unhealthy” only.

This is **content-based** (item attributes + shared category limits). Co-purchase or
matrix-factorization CF can reuse the same tier scaffold later.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from app.config import settings
from app.models import (
    ClassifierPrediction,
    HealthierSubstituteResult,
    KnpmLabel,
    NutritionData,
    OcrResult,
    SubstituteProduct,
)
from app.services.knpm_labeller import KNPM_CLASSIFICATION_LESS_HEALTHY, classify_with_knpm
from app.services.reference_catalog_db import iter_reference_products_with_nutrition_db
from app.utils.pack_description import normalize_pack_description

logger = logging.getLogger(__name__)


def _catalog_cache_key() -> tuple[str, str]:
    return ("__fallback__", "thresholds_fallback")


def _norm_taxon(s: str | None) -> str:
    return (s or "").strip().casefold()


@dataclass(frozen=True)
class _Cand:
    product_name: str
    nutrition: "object"  # NutritionData
    class_name: str | None
    subclass_name: str | None
    sub_type: str | None
    octagons: list[str]
    below: bool


# fixed fallback scope cache
_catalog_cache: dict[tuple[str, str], list[_Cand]] = {}


def _scan_class_subclass(ocr: OcrResult) -> tuple[str | None, str | None]:
    c, s = ocr.class_name, ocr.subclass_name
    cp: ClassifierPrediction | None = ocr.classifier_prediction
    if cp is not None:
        if not c and cp.class_name:
            c = cp.class_name
        if not s and cp.subclass_name:
            s = cp.subclass_name
    return c, s


def _exceeded_tags(knpm: KnpmLabel | None) -> list[str]:
    if knpm is None:
        return []
    tags: list[str] = []
    for o in knpm.octagons or []:
        if o == "high_in_sugar":
            tags.append("total_sugars_or_sweeteners")
        elif o == "high_in_salt":
            tags.append("sodium")
        elif o == "high_in_fat":
            tags.append("total_fat_saturated_or_trans")
    # De-dupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _should_run(knpm: KnpmLabel | None) -> bool:
    if knpm is None:
        return False
    cls = str(knpm.classification or "").strip().lower()
    return cls in (KNPM_CLASSIFICATION_LESS_HEALTHY, "not healthy")


def _build_catalog_candidates() -> list[_Cand]:
    out: list[_Cand] = []
    for pname, nut, raw in iter_reference_products_with_nutrition_db():
        if not isinstance(nut, NutritionData):
            continue
        ref_class = (raw.get("class_name") or None)
        ref_subclass = (raw.get("subclass_name") or None)
        label = classify_with_knpm(
            nut,
            has_trans_fats=False,
            has_sweeteners=False,
        )
        octs = list(label.octagons or [])
        below = len(octs) == 0 and label.classification == "healthy"
        out.append(
            _Cand(
                product_name=pname,
                nutrition=nut,
                class_name=ref_class,
                subclass_name=ref_subclass,
                sub_type=(raw.get("sub_type") or "").strip() or None,
                octagons=octs,
                below=below,
            )
        )
    return out


def _sort_key(c: _Cand) -> tuple[int, int, str]:
    """Below-threshold first, then fewer octagons, then stable name order."""
    return (0 if c.below else 1, len(c.octagons), c.product_name.casefold())


def _pick_from_tier(
    tier_cands: list[_Cand],
    exclude_norm: set[str],
    need: int,
) -> list[_Cand]:
    usable = [c for c in tier_cands if normalize_pack_description(c.product_name) not in exclude_norm]
    usable.sort(key=_sort_key)
    return usable[:need]


def _norm_key(name: str) -> str:
    return normalize_pack_description(name)


def _zero_octagon_candidate(c: _Cand) -> bool:
    """Substitutes must have no KNPM octagons on the reference row."""
    return len(c.octagons) == 0


def build_healthier_substitutes(
    ocr: OcrResult,
    *,
    has_trans_fats: bool = False,
    has_sweeteners: bool = False,
) -> HealthierSubstituteResult | None:
    """
    Build substitute list for an OCR result. Returns None when disabled or not applicable.

    ``has_trans_fats`` / ``has_sweeteners`` are ignored here (already in ``knpm_label``);
    kept for API symmetry with the OCR pipeline.
    """
    _ = (has_trans_fats, has_sweeteners)
    if not settings.substitute_recommendations_enabled:
        return HealthierSubstituteResult(
            triggered=False,
            skip_reason="Substitute recommendations disabled (SUBSTITUTE_RECOMMENDATIONS_ENABLED).",
        )

    knpm = ocr.knpm_label
    if not _should_run(knpm):
        reason = "Product is not flagged as less healthy (KNPM), or classification unavailable."
        if knpm and knpm.classification == "unknown":
            reason = "KNPM classification unknown — substitutes not suggested."
        if knpm and knpm.classification == "healthy":
            reason = "Product is within assessed KNPM limits — no substitutes suggested."
        return HealthierSubstituteResult(triggered=False, skip_reason=reason)

    max_n = max(1, min(20, int(settings.substitute_max_results)))
    min_n = max(1, min(max_n, int(settings.substitute_min_results)))

    try:
        ck = _catalog_cache_key()
        if ck not in _catalog_cache:
            _catalog_cache[ck] = _build_catalog_candidates()
        all_cands = _catalog_cache[ck]
    except Exception:
        logger.exception("Failed to build substitute catalog")
        return HealthierSubstituteResult(
            triggered=True,
            skip_reason="Catalog load failed — check PostgreSQL catalog.product_nutrition (see REFERENCE_CATALOG_* env).",
            exceeded_nutrient_summary=_exceeded_tags(knpm),
        )

    scan_name = (ocr.product_info.name or "").strip() if ocr.product_info else ""
    exclude: set[str] = set()
    if scan_name:
        exclude.add(normalize_pack_description(scan_name))
        if ocr.product_info and ocr.product_info.brand:
            combo = f"{ocr.product_info.brand.strip()} {scan_name}".strip()
            exclude.add(normalize_pack_description(combo))

    scan_c, scan_s = _scan_class_subclass(ocr)
    nc, ns = _norm_taxon(scan_c), _norm_taxon(scan_s)

    tier1: list[_Cand] = []
    tier2: list[_Cand] = []
    tier3: list[_Cand] = []
    for c in all_cands:
        cc, ss = _norm_taxon(c.class_name), _norm_taxon(c.subclass_name)
        in_sub = bool(ns) and ss == ns
        in_class = bool(nc) and cc == nc
        if in_sub:
            tier1.append(c)
        elif in_class and (not ns or ss != ns):
            tier2.append(c)
        else:
            tier3.append(c)

    tier1 = [c for c in tier1 if _zero_octagon_candidate(c)]
    tier2 = [c for c in tier2 if _zero_octagon_candidate(c)]
    tier3 = [c for c in tier3 if _zero_octagon_candidate(c)]

    chosen: list[tuple[int, _Cand]] = []
    seen_keys: set[str] = set()

    def _add_from_pool(tier_num: int, pool: list[_Cand]) -> None:
        need = max_n - len(chosen)
        if need <= 0:
            return
        for c in _pick_from_tier(pool, exclude, need):
            k = _norm_key(c.product_name)
            if k in seen_keys:
                continue
            seen_keys.add(k)
            chosen.append((tier_num, c))

    # Strict taxonomy waterfall: subclass → class → full catalog (within max_n).
    _add_from_pool(1, tier1)
    if len(chosen) < min_n:
        _add_from_pool(2, tier2)
    if len(chosen) < min_n:
        _add_from_pool(3, tier3)

    chosen = chosen[:max_n]

    if not chosen:
        return HealthierSubstituteResult(
            triggered=True,
            exceeded_nutrient_summary=_exceeded_tags(knpm),
            tier_used=3,
            no_close_substitutes=True,
            substitutes=[],
            skip_reason=(
                "No reference products with zero KNPM octagons were found in the PostgreSQL "
                "reference table for this taxonomy search."
            ),
        )

    tier_used = max(t for t, _ in chosen)
    subs = [
        SubstituteProduct(
            product_name=c.product_name,
            tier=tier_num,
            class_name=c.class_name,
            subclass_name=c.subclass_name,
            octagon_count=len(c.octagons),
            octagons=c.octagons,
            below_knpm_thresholds=c.below,
            sub_type=c.sub_type,
            form=None,
        )
        for tier_num, c in chosen
    ]

    no_close = tier_used > 1 or (not any(s.below_knpm_thresholds for s in subs))

    approach = (
        "Substitutes are limited to reference rows with zero KNPM octagons. "
        "Order: same subclass, then same class, then full catalog; within each tier, "
        "sort by healthy profile then name."
    )

    return HealthierSubstituteResult(
        triggered=True,
        exceeded_nutrient_summary=_exceeded_tags(knpm),
        tier_used=tier_used,
        no_close_substitutes=no_close,
        inferred_scan_form=None,
        inferred_substitute_use_context=None,
        substitutes_include_other_forms=False,
        substitutes_include_pantry_liquids=False,
        substitutes=subs,
        approach_note=approach,
    )


def template_explanation(ocr: OcrResult, result: HealthierSubstituteResult) -> str:
    """Non-LLM fallback copy."""
    parts: list[str] = []
    knpm = ocr.knpm_label
    if knpm and knpm.octagons:
        parts.append(
            "This product triggered KNPM warnings: "
            + ", ".join(o.replace("_", " ").lower() for o in knpm.octagons)
            + "."
        )
    if result.substitutes:
        better = [s.product_name for s in result.substitutes if s.below_knpm_thresholds]
        if better:
            parts.append(
                "Alternatives such as "
                + ", ".join(better[:3])
                + ("…" if len(better) > 3 else "")
                + " show no black octagons under KNPM on the reference nutrition we have."
            )
        else:
            parts.append(
                "Listed alternatives have no black octagons on their reference nutrition rows."
            )
    if result.no_close_substitutes:
        parts.append(
            "We widened the search beyond your exact retail sub-category because no close below-threshold matches were found."
        )
    return " ".join(parts) if parts else "See substitute list for reference products with better KNPM profiles."
