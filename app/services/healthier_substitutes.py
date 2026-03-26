"""
Healthier product substitutes: tiered catalog search + KNPM ranking.

Tiers (retailer taxonomy when reference ``product_name`` exactly matches a POS SKU line
after ``normalize_pack_description``):

- **Tier 1**: same POS ``subclass_name`` as the scan.
- **Tier 2**: same POS ``class_name``, different subclass (or Tier 1 empty).
- **Tier 3**: full reference pool (still scored with the **scan's** KNPM category limits).

Within each tier, prefer the same **physical form** (liquid / solid / paste), then—for
**drink-like** liquid scans—prefer **beverages** (juice, soft drink, milk, …) over pantry
liquids (oils, vinegar) using ``sub_type`` and product name. Then prefer products **below**
numeric KNPM thresholds, then lowest ``octagon_count``. Ingredient-only flags are **not**
applied to catalog rows (no ingredients in reference CSV).

This is **content-based** (item attributes + shared category limits). Co-purchase or
matrix-factorization CF can reuse the same tier scaffold later.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from app.config import settings
from app.models import (
    FoodclassesBiLstmPrediction,
    HealthierSubstituteResult,
    KnpmLabel,
    NutritionData,
    OcrResult,
    SubstituteProduct,
)
from app.services.knpm_labeller import classify_with_knpm
from app.services.knpm_category_thresholds import resolve_knpm_thresholds_for_extraction
from app.services.reference_nutrition_lookup import iter_reference_products_with_nutrition
from app.services.supermarket_lookup import pos_taxonomy_for_normalized_description
from app.utils.pos_description import normalize_pack_description
from app.utils.product_form import canonical_food_form, form_sort_rank, infer_scan_form
from app.utils.substitute_practicality import (
    infer_beverage_like_liquid_scan,
    is_probable_pantry_liquid_substitute,
    liquid_beverage_practicality_rank,
)

logger = logging.getLogger(__name__)


def _catalog_cache_key(threshold_row: object | None, thresholds_source: str | None) -> tuple[str, str]:
    if threshold_row is None:
        return ("__none__", thresholds_source or "hardcoded_fallback")
    num = getattr(threshold_row, "category_number", None) or "__unknown__"
    return (str(num), thresholds_source or "hardcoded_fallback")


def _norm_taxon(s: str | None) -> str:
    return (s or "").strip().casefold()


@dataclass(frozen=True)
class _Cand:
    product_name: str
    nutrition: "object"  # NutritionData
    class_name: str | None
    subclass_name: str | None
    sub_type: str | None
    form: str | None
    octagons: list[str]
    below: bool


# (KNPM category_number or sentinel, thresholds_source) → scored catalog
_catalog_cache: dict[tuple[str, str], list[_Cand]] = {}


def _scan_class_subclass(ocr: OcrResult) -> tuple[str | None, str | None]:
    c, s = ocr.class_name, ocr.subclass_name
    fc: FoodclassesBiLstmPrediction | None = ocr.foodclasses_bilstm_prediction
    if fc is not None:
        min_c = float(settings.foodclasses_bilstm_min_class_confidence)
        min_s = float(settings.foodclasses_bilstm_min_subclass_confidence)
        if not c and fc.class_name and (fc.class_confidence or 0) >= min_c:
            c = fc.class_name
        if not s and fc.subclass_name and (fc.subclass_confidence or 0) >= min_s:
            s = fc.subclass_name
    return c, s


def _exceeded_tags(knpm: KnpmLabel | None) -> list[str]:
    if knpm is None:
        return []
    tags: list[str] = []
    for o in knpm.octagons or []:
        if o == "HIGH_IN_SUGAR":
            tags.append("total_sugars_or_sweeteners")
        elif o == "HIGH_IN_SALT":
            tags.append("sodium")
        elif o == "HIGH_IN_FAT":
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
    return knpm.classification == "LESS_HEALTHY"


def _build_catalog_candidates(threshold_row: object | None, thresholds_source: str | None) -> list[_Cand]:
    out: list[_Cand] = []
    for pname, nut, raw in iter_reference_products_with_nutrition():
        if not isinstance(nut, NutritionData):
            continue
        nkey = normalize_pack_description(pname)
        pos_c, pos_s = pos_taxonomy_for_normalized_description(nkey)
        label = classify_with_knpm(
            nut,
            has_trans_fats=False,
            has_sweeteners=False,
            threshold_row=threshold_row,
            thresholds_source=thresholds_source,  # type: ignore[arg-type]
            category_match_score=None,
        )
        octs = list(label.octagons or [])
        below = len(octs) == 0 and label.classification == "FIT_FOR_CONSUMPTION"
        out.append(
            _Cand(
                product_name=pname,
                nutrition=nut,
                class_name=pos_c,
                subclass_name=pos_s,
                sub_type=(raw.get("sub_type") or "").strip() or None,
                form=(raw.get("form") or "").strip() or None,
                octagons=octs,
                below=below,
            )
        )
    return out


def _sort_key_for_scan(ocr: OcrResult, scan_form: str | None, c: _Cand) -> tuple[int, int, int, int, str]:
    """Form match → beverage practicality (drink-like liquids) → below-threshold → octagons → name."""
    fr = form_sort_rank(scan_form, c.form)
    pr = liquid_beverage_practicality_rank(ocr, scan_form, c)
    return (fr, pr, 0 if c.below else 1, len(c.octagons), c.product_name.casefold())


def _pick_from_tier(
    tier_cands: list[_Cand],
    exclude_norm: set[str],
    need: int,
    ocr: OcrResult,
    scan_form: str | None,
) -> list[_Cand]:
    usable = [c for c in tier_cands if normalize_pack_description(c.product_name) not in exclude_norm]
    usable.sort(key=lambda c: _sort_key_for_scan(ocr, scan_form, c))

    # Drink-like liquid scans: use only “beverage-practical” rows (rank < 2) until we run out,
    # so oils/vinegars do not fill slots while enough juices/drinks exist in this tier.
    if infer_beverage_like_liquid_scan(ocr, scan_form):
        good = [c for c in usable if liquid_beverage_practicality_rank(ocr, scan_form, c) < 2]
        if len(good) >= need:
            return good[:need]
        seen: set[str] = set()
        out: list[_Cand] = []
        for c in good:
            k = normalize_pack_description(c.product_name)
            if k in seen:
                continue
            seen.add(k)
            out.append(c)
            if len(out) >= need:
                return out
        for c in usable:
            if len(out) >= need:
                break
            k = normalize_pack_description(c.product_name)
            if k in seen:
                continue
            seen.add(k)
            out.append(c)
        return out

    return usable[:need]


def _norm_key(name: str) -> str:
    return normalize_pack_description(name)


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
        if knpm and knpm.classification == "UNKNOWN":
            reason = "KNPM classification unknown — substitutes not suggested."
        if knpm and knpm.classification == "FIT_FOR_CONSUMPTION":
            reason = "Product is within assessed KNPM limits — no substitutes suggested."
        return HealthierSubstituteResult(triggered=False, skip_reason=reason)

    max_n = max(1, min(20, int(settings.substitute_max_results)))
    min_n = max(1, min(max_n, int(settings.substitute_min_results)))

    thr_row, thr_source, _ = resolve_knpm_thresholds_for_extraction(
        ocr.product_info,
        ocr.supermarket_classification,
    )

    try:
        ck = _catalog_cache_key(thr_row, thr_source)
        if ck not in _catalog_cache:
            _catalog_cache[ck] = _build_catalog_candidates(thr_row, thr_source)
        all_cands = _catalog_cache[ck]
    except Exception:
        logger.exception("Failed to build substitute catalog")
        return HealthierSubstituteResult(
            triggered=True,
            skip_reason="Catalog load failed — check reference_nutrition_lookup.csv.",
            exceeded_nutrient_summary=_exceeded_tags(knpm),
        )

    scan_name = (ocr.product_info.name or "").strip() if ocr.product_info else ""
    exclude: set[str] = set()
    if scan_name:
        exclude.add(normalize_pack_description(scan_name))
        if ocr.product_info and ocr.product_info.brand:
            combo = f"{ocr.product_info.brand.strip()} {scan_name}".strip()
            exclude.add(normalize_pack_description(combo))

    scan_form = infer_scan_form(ocr)
    beverage_context = infer_beverage_like_liquid_scan(ocr, scan_form)

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

    tier1_any_below = any(
        c.below for c in tier1 if _norm_key(c.product_name) not in exclude
    )

    chosen: list[tuple[int, _Cand]] = []
    seen_keys: set[str] = set()

    def _add_tier(tier_num: int, picks: list[_Cand]) -> None:
        for c in picks:
            k = _norm_key(c.product_name)
            if k in seen_keys:
                continue
            seen_keys.add(k)
            chosen.append((tier_num, c))

    # Tier 1: same subclass — prefer below-threshold (via sort) when any exist.
    if tier1_any_below or not tier1:
        _add_tier(1, _pick_from_tier(tier1, exclude, max_n, ocr, scan_form))
    # If subclass pool exists but nothing is below threshold, skip filling with "bad" T1-only
    # and widen to class (T2) then catalog (T3), per product policy.
    elif tier1 and not tier1_any_below:
        _add_tier(2, _pick_from_tier(tier2, exclude, max_n, ocr, scan_form))
        if len(chosen) < min_n:
            _add_tier(3, _pick_from_tier(tier3, exclude, max_n - len(chosen), ocr, scan_form))

    if len(chosen) < min_n:
        _add_tier(2, _pick_from_tier(tier2, exclude, max_n - len(chosen), ocr, scan_form))
    if len(chosen) < min_n:
        _add_tier(3, _pick_from_tier(tier3, exclude, max_n - len(chosen), ocr, scan_form))

    chosen = chosen[:max_n]

    if not chosen:
        return HealthierSubstituteResult(
            triggered=True,
            exceeded_nutrient_summary=_exceeded_tags(knpm),
            tier_used=3,
            no_close_substitutes=True,
            substitutes=[],
            skip_reason="No alternative products with nutrition data found in the reference catalog.",
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
            form=c.form,
        )
        for tier_num, c in chosen
    ]

    no_close = tier_used > 1 or (not any(s.below_knpm_thresholds for s in subs))

    other_forms = False
    if scan_form:
        for s in subs:
            cf = canonical_food_form(s.form)
            if cf is not None and cf != scan_form:
                other_forms = True
                break

    pantry_in_list = False
    if beverage_context:
        for s in subs:
            if is_probable_pantry_liquid_substitute(s.product_name, s.sub_type):
                pantry_in_list = True
                break

    approach = (
        "Ranked from reference_nutrition_lookup using POS taxonomy (exact description match), "
        "the same KNPM category limits as this scan, and—when possible—the same pack form "
        "(liquid / solid / paste). For drink-like liquid scans, **beverages** (juice, soft drink, "
        "milk, …) are preferred over **pantry liquids** (oils, vinegar) using reference ``sub_type`` "
        "and names. Collaborative filtering from user/scan co-occurrence can be layered on top later."
    )

    return HealthierSubstituteResult(
        triggered=True,
        exceeded_nutrient_summary=_exceeded_tags(knpm),
        tier_used=tier_used,
        no_close_substitutes=no_close,
        inferred_scan_form=scan_form,
        inferred_substitute_use_context="beverage_drink" if beverage_context else None,
        substitutes_include_other_forms=other_forms,
        substitutes_include_pantry_liquids=pantry_in_list,
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
                + " stay within the same nutrient limits for this food category and show fewer or no black octagons."
            )
        else:
            parts.append(
                "Listed alternatives still have some warnings but may have fewer octagons than your product within our reference set."
            )
    if result.no_close_substitutes:
        parts.append(
            "We widened the search beyond your exact retail sub-category because no close below-threshold matches were found."
        )
    if result.inferred_scan_form:
        parts.append(
            f"We ranked alternatives to prefer the same pack form ({result.inferred_scan_form}) when the reference data includes it."
        )
    if result.substitutes_include_other_forms:
        parts.append(
            "Some suggestions may be a different form (e.g. solid) if few same-form options met the nutrition criteria."
        )
    if result.substitutes_include_pantry_liquids:
        parts.append(
            "Some listed liquids are oils or condiments—only shown because the database returned few drink-style alternatives under the same KNPM limits."
        )
    return " ".join(parts) if parts else "See substitute list for reference products with better KNPM profiles."
