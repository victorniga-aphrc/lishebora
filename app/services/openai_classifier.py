"""Runtime product classifier using OpenAI structured output.

Sole runtime classifier (BiLSTM is intentionally not wired in; its files remain in
the repo only for possible future re-activation).

Design:
- Label universe is pulled from ``catalog.product_nutrition`` so the model can only
  return labels that actually exist in the live taxonomy.
- Predictions are persisted to ``catalog.classification_cache`` keyed by a
  normalized name+brand cache key. Cache hits skip the LLM call entirely.
- Self-reported confidence (1-5) and a ``needs_review`` flag are exposed for the UI.
- Every code path that previously returned ``None`` now returns a
  ``ClassifierPrediction`` with empty labels and a ``reason`` describing why no
  classification was produced (key missing, API error, model declined, ...).
  This keeps the UI honest: the user always sees "OpenAI ran, here's why nothing
  was returned" rather than the misleading "no classifier ran".

Adapted from the offline pipeline at ``scripts/postgres_data_pipeline.py``.
"""

from __future__ import annotations

import json
import logging
import re
import time
import unicodedata
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine
from app.models import ClassifierPrediction, Ingredient, NutritionData
from app.utils.product_text import compose_product_query_text

logger = logging.getLogger(__name__)


_OPENAI_SYSTEM_PROMPT = (
    "You classify Kenyan supermarket food/beverage products into a FIXED taxonomy.\n"
    "You MUST pick values strictly from the provided allow-lists, or return null when no listed value fits.\n"
    "Output strict JSON only (no prose).\n\n"
    "Available evidence (any subset may be present): product name, brand, ingredients list, "
    "nutrition per 100 g/ml, visual labels and product-type hints from the image.\n"
    "Use ALL evidence available, in this priority order:\n"
    "  A. A clear FOOD NOUN in the product name (e.g. 'flour', 'biscuit', 'sauce', 'rice', 'milk').\n"
    "  B. The visual product-type hint from the image (e.g. 'fruit juice', 'yoghurt drink', 'biscuit').\n"
    "  C. The ingredients list (e.g. 'Mango pulp, Sugar, Water, Acidity regulator' = a fruit juice/drink).\n"
    "  D. The nutrition profile (e.g. very high sugar + non-zero fat + cocoa = chocolate confection).\n"
    "  E. The brand. Brand words alone may be misleading and must never override A-D.\n"
    "If the name is ambiguous (no clear food noun, e.g. 'Delight', 'Premium', 'Classic'), DO NOT decline "
    "immediately - first try to classify using B, C, D. Only return null when B, C, D are also missing "
    "or unhelpful.\n\n"
    "Disambiguation rules (apply in order):\n"
    "1. The actual FOOD NOUN (when present) ALWAYS wins over brand words.\n"
    "2. 'CARRS TABLE WATER' is a brand of CRACKERS/BISCUITS, not water. Tokens like 'BITE', 'BITES', "
    "'BISCUIT', 'BISCUITS', 'CRACKER', 'CRACKERS', 'COOKIE', 'COOKIES', 'WAFER', 'WAFERS' indicate "
    "baked snacks even when the product or brand contains the word 'WATER'.\n"
    "3. 'ESSENCE', 'EXTRACT', 'FLAVOUR', 'FLAVOR', 'FLAVOURING', 'FLAVORING' = a flavour concentrate "
    "(e.g. 'Vanilla Essence', 'Chocolate Essence'). These are NOT 'baking powder' or 'baking soda'. "
    "Pick a flavouring/essence/baking-additive subclass ONLY if the allow-list contains an explicit "
    "essence/flavour option; otherwise return null for the affected fields.\n"
    "4. 'FLOUR' (including 'self-raising flour', 'self raising flour', 'all-purpose flour', "
    "'whole wheat flour', 'maida', 'atta') = a FLOUR. Self-raising flour is still flour, NOT 'baking "
    "powder'. Map to a flour class/subclass.\n"
    "5. 'BAKING POWDER', 'BAKING SODA', 'BICARBONATE OF SODA', 'YEAST' = leavening agents only. Do NOT "
    "use these labels for flour, essence, sugar, salt, or any non-leavening item.\n"
    "6. 'MINERAL WATER', 'DRINKING WATER', 'BOTTLED WATER', 'STILL WATER', 'SPARKLING WATER' = drinking "
    "water; ONLY if the product is actually water (see rule 2).\n"
    "7. 'JUICE', 'SODA', 'COLA', 'CORDIAL', 'SQUASH' = non-alcoholic beverages; not water.\n"
    "8. 'OIL' (cooking oil, sunflower oil, olive oil) = edible oil; not 'fat spread' or 'margarine'.\n"
    "9. 'MILK' = dairy milk; flavoured milk drinks are still milk-based beverages, not water/soda.\n"
    "10. 'TEA', 'COFFEE' = the tea/coffee category; 'COFFEE BREAK CAPU' is instant coffee, not biscuit.\n"
    "11. 'NJAHI' = black beans (a Kenyan pulse); classify as a pulse/legume, not a milk/yoghurt brand.\n"
    "12. 'ORCHID VALLEY' is a Kenyan brand of FRUIT JUICES and JUICE DRINKS; if the brand is "
    "'ORCHID VALLEY' (or similar known juice brands like 'PICANA', 'AFIA', 'MINUTE MAID', 'DEL MONTE'), "
    "classify as a fruit juice/juice drink even when the rest of the name is a flavour word "
    "('Delight', 'Tropical', 'Mixed Fruit').\n"
    "13. If you are not at least somewhat confident OR the allow-lists do not contain a suitable label, "
    "set the affected fields to null and lower the confidence score.\n\n"
    "Confidence (1-5):\n"
    "  5 = definitely correct (clear food noun + no conflicting cues, or strong agreement across A-D)\n"
    "  4 = very likely (one strong signal, no conflicts)\n"
    "  3 = leaning correct (some ambiguity, used B/C/D to resolve)\n"
    "  2 = guess (weak signals, ambiguous brand)\n"
    "  1 = essentially unknown / forced choice\n"
    "Be honest. Low confidence is preferred over a wrong high-confidence answer."
)


# ----- Cache key normalization ---------------------------------------------------


_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)


def _build_cache_key(product_name: str | None, brand: str | None) -> str | None:
    """Normalize (name, brand) into a stable cache key."""
    parts = [p for p in (brand, product_name) if p]
    if not parts:
        return None
    raw = " ".join(parts)
    nfkc = unicodedata.normalize("NFKC", raw).casefold()
    no_punct = _PUNCT_RE.sub(" ", nfkc)
    collapsed = _WS_RE.sub(" ", no_punct).strip()
    return collapsed or None


# ----- Label universe ------------------------------------------------------------


class _LabelUniverseCache:
    """Module-level cache for the label allow-lists pulled from the DB.

    Refreshed lazily; in production we don't expect this to change between
    deploys, so a small TTL is fine.
    """

    __slots__ = ("ts", "classes", "subclasses", "novas")

    def __init__(self) -> None:
        self.ts: float = 0.0
        self.classes: list[str] = []
        self.subclasses: list[str] = []
        self.novas: list[str] = []


_universe = _LabelUniverseCache()
_UNIVERSE_TTL_S = 600.0  # 10 minutes


def _load_label_universe(db: Session | None) -> tuple[list[str], list[str], list[str]]:
    """Load distinct class_name / subclass_name / nova values from product_nutrition.

    Cached for ``_UNIVERSE_TTL_S`` seconds. If the DB is unreachable but we have a
    previously cached copy in memory, return that stale copy instead of failing —
    this keeps the classifier working through transient DB outages.
    """
    now = time.time()
    have_cached = bool(_universe.classes or _universe.subclasses or _universe.novas)
    if have_cached and (now - _universe.ts) < _UNIVERSE_TTL_S:
        return _universe.classes, _universe.subclasses, _universe.novas

    table = settings.reference_catalog_qualified_sql
    sql = text(
        f"""
        SELECT
            COALESCE(
                ARRAY(SELECT DISTINCT class_name FROM {table}
                      WHERE class_name IS NOT NULL AND TRIM(class_name) <> ''
                      ORDER BY class_name),
                ARRAY[]::text[]
            ) AS classes,
            COALESCE(
                ARRAY(SELECT DISTINCT subclass_name FROM {table}
                      WHERE subclass_name IS NOT NULL AND TRIM(subclass_name) <> ''
                      ORDER BY subclass_name),
                ARRAY[]::text[]
            ) AS subclasses,
            COALESCE(
                ARRAY(SELECT DISTINCT nova FROM {table}
                      WHERE nova IS NOT NULL AND TRIM(nova) <> ''
                      ORDER BY nova),
                ARRAY[]::text[]
            ) AS novas
        """
    )
    try:
        if db is not None:
            row = db.execute(sql).fetchone()
        else:
            with engine.begin() as conn:
                row = conn.execute(sql).fetchone()
    except SQLAlchemyError:
        logger.exception("Failed to load classifier label universe")
        if have_cached:
            logger.warning(
                "Using stale label universe (age %.0fs) because DB refresh failed",
                now - _universe.ts,
            )
            return _universe.classes, _universe.subclasses, _universe.novas
        return [], [], []

    if row is None:
        if have_cached:
            return _universe.classes, _universe.subclasses, _universe.novas
        return [], [], []
    _universe.classes = list(row.classes or [])
    _universe.subclasses = list(row.subclasses or [])
    _universe.novas = list(row.novas or [])
    _universe.ts = now
    return _universe.classes, _universe.subclasses, _universe.novas


# ----- Cache I/O -----------------------------------------------------------------


def _read_cache(cache_key: str, db: Session | None) -> Optional[ClassifierPrediction]:
    table = settings.classification_cache_qualified_sql
    sql = text(
        f"""
        SELECT product_name, class_name, subclass_name, nova,
               confidence, needs_review, reason, source, model_used
        FROM {table}
        WHERE cache_key = :cache_key
        LIMIT 1
        """
    )
    try:
        if db is not None:
            row = db.execute(sql, {"cache_key": cache_key}).fetchone()
        else:
            with engine.begin() as conn:
                row = conn.execute(sql, {"cache_key": cache_key}).fetchone()
    except SQLAlchemyError:
        logger.exception("Failed to read classification_cache for key=%r", cache_key)
        return None
    if row is None:
        return None
    return ClassifierPrediction(
        class_name=row.class_name,
        subclass_name=row.subclass_name,
        nova=row.nova,
        confidence=row.confidence,
        needs_review=bool(row.needs_review),
        reason=row.reason,
        source=row.source or "cache",
        model_used=row.model_used,
        cached=True,
        input_text=row.product_name or "",
    )


def _write_cache(
    *,
    cache_key: str,
    product_name: str,
    brand: str | None,
    pred: ClassifierPrediction,
    db: Session | None,
) -> None:
    table = settings.classification_cache_qualified_sql
    sql = text(
        f"""
        INSERT INTO {table}
            (cache_key, product_name, brand, class_name, subclass_name, nova,
             confidence, needs_review, reason, model_used, source, created_at, updated_at)
        VALUES
            (:cache_key, :product_name, :brand, :class_name, :subclass_name, :nova,
             :confidence, :needs_review, :reason, :model_used, :source, NOW(), NOW())
        ON CONFLICT (cache_key) DO UPDATE SET
            product_name = EXCLUDED.product_name,
            brand = EXCLUDED.brand,
            class_name = EXCLUDED.class_name,
            subclass_name = EXCLUDED.subclass_name,
            nova = EXCLUDED.nova,
            confidence = EXCLUDED.confidence,
            needs_review = EXCLUDED.needs_review,
            reason = EXCLUDED.reason,
            model_used = EXCLUDED.model_used,
            source = EXCLUDED.source,
            updated_at = NOW()
        """
    )
    params = {
        "cache_key": cache_key,
        "product_name": product_name,
        "brand": brand,
        "class_name": pred.class_name,
        "subclass_name": pred.subclass_name,
        "nova": pred.nova,
        "confidence": pred.confidence,
        "needs_review": pred.needs_review,
        "reason": pred.reason,
        "model_used": pred.model_used,
        "source": pred.source,
    }
    try:
        if db is not None:
            db.execute(sql, params)
            db.commit()
        else:
            with engine.begin() as conn:
                conn.execute(sql, params)
    except SQLAlchemyError:
        logger.exception("Failed to write classification_cache for key=%r", cache_key)


# ----- OpenAI call ---------------------------------------------------------------


def _build_evidence_block(
    *,
    line: str,
    brand: str | None,
    ingredients: list[Ingredient] | list[str] | None,
    nutrition: NutritionData | None,
    visual_labels: list[str] | None,
    visual_product_type: str | None,
) -> str:
    """Render the optional non-name evidence (ingredients, nutrition, visuals) so
    the model can use it when the product name alone is ambiguous."""
    parts: list[str] = [f"Product name: {line!r}"]
    if brand:
        parts.append(f"Brand: {brand!r}")
    if visual_product_type:
        parts.append(f"Visual product type (image hint): {visual_product_type!r}")
    if visual_labels:
        cleaned = [v for v in visual_labels if v and str(v).strip()]
        if cleaned:
            parts.append(
                "Visual labels (image hints): "
                + json.dumps(cleaned[:12], ensure_ascii=False)
            )
    if ingredients:
        names: list[str] = []
        for ing in ingredients:
            n = ing.name if isinstance(ing, Ingredient) else str(ing)
            if n and n.strip():
                names.append(n.strip())
        if names:
            parts.append(
                "Ingredients (in declared order): "
                + json.dumps(names[:24], ensure_ascii=False)
            )
    if nutrition is not None:
        nut_dict = {
            "total_fat_g_per_100": nutrition.total_fat,
            "trans_fat_g_per_100": nutrition.trans_fat,
            "total_sugar_g_per_100": nutrition.total_sugar,
            "sodium_g_per_100": nutrition.sodium,
        }
        nut_dict = {k: v for k, v in nut_dict.items() if v is not None}
        if nut_dict:
            parts.append(
                "Nutrition per 100 g/ml: " + json.dumps(nut_dict, ensure_ascii=False)
            )
    return "\n".join(parts)


def _call_openai(
    *,
    line: str,
    brand: str | None,
    ingredients: list[Ingredient] | list[str] | None,
    nutrition: NutritionData | None,
    visual_labels: list[str] | None,
    visual_product_type: str | None,
    classes: list[str],
    subclasses: list[str],
    novas: list[str],
    model: str,
    timeout_s: float,
) -> tuple[str | None, str | None, str | None, int, str] | None:
    """Make the chat-completions call. Returns
    ``(class, subclass, nova, confidence, reason)`` or ``None`` on failure.
    """
    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except ImportError:
        logger.warning("openai package not installed; classifier disabled")
        return None

    api_key = settings.openai_api_key
    if not api_key:
        logger.warning("OPENAI_API_KEY not set; classifier disabled")
        return None

    client = OpenAI(api_key=api_key, timeout=timeout_s)

    evidence = _build_evidence_block(
        line=line,
        brand=brand,
        ingredients=ingredients,
        nutrition=nutrition,
        visual_labels=visual_labels,
        visual_product_type=visual_product_type,
    )

    user = (
        f"{evidence}\n\n"
        "Allowed class_name values:\n"
        f"{json.dumps(classes, ensure_ascii=False)}\n\n"
        "Allowed subclass_name values:\n"
        f"{json.dumps(subclasses, ensure_ascii=False)}\n\n"
        "Allowed nova values:\n"
        f"{json.dumps(novas, ensure_ascii=False)}\n\n"
        "Apply the disambiguation rules above. Use ingredients, visual labels, and "
        "nutrition signals when the product name is ambiguous. Output JSON ONLY with "
        "this exact schema:\n"
        "{\n"
        '  "class_name": "<one of class_name allow-list, or null>",\n'
        '  "subclass_name": "<one of subclass_name allow-list, or null>",\n'
        '  "nova": "<one of nova allow-list, or null>",\n'
        '  "confidence": <integer 1 to 5>,\n'
        '  "reason": "<<= 120 chars, the strongest signal you used>"\n'
        "}"
    )

    try:
        r = client.chat.completions.create(
            model=model,
            temperature=0.0,
            max_tokens=300,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _OPENAI_SYSTEM_PROMPT},
                {"role": "user", "content": user},
            ],
        )
    except Exception:
        logger.exception("OpenAI classifier request failed for line=%r", line[:80])
        return None

    text_out = (r.choices[0].message.content or "").strip()
    if not text_out:
        return None
    try:
        data = json.loads(text_out)
    except json.JSONDecodeError:
        logger.warning("OpenAI classifier returned non-JSON: %r", text_out[:120])
        return None
    if not isinstance(data, dict):
        return None

    cls = data.get("class_name") or None
    sub = data.get("subclass_name") or None
    nova = data.get("nova") or None
    cls_s = str(cls).strip() if cls else None
    sub_s = str(sub).strip() if sub else None
    nova_s = str(nova).strip() if nova else None

    classes_set = set(classes)
    subclasses_set = set(subclasses)
    novas_set = set(novas)

    if cls_s and cls_s not in classes_set:
        cls_s = None
    if sub_s and sub_s not in subclasses_set:
        sub_s = None
    if nova_s and nova_s not in novas_set:
        nova_s = None

    raw_conf = data.get("confidence")
    try:
        conf_int = int(raw_conf) if raw_conf is not None else 1
    except (TypeError, ValueError):
        conf_int = 1
    conf_int = max(1, min(5, conf_int))

    reason = str(data.get("reason") or "").strip()
    if len(reason) > 200:
        reason = reason[:200]

    return cls_s, sub_s, nova_s, conf_int, reason or ""


# ----- Public entry point --------------------------------------------------------


def _empty_prediction(line: str, reason: str) -> ClassifierPrediction:
    """Build a no-label prediction so the UI can surface *why* nothing was classified."""
    return ClassifierPrediction(
        class_name=None,
        subclass_name=None,
        nova=None,
        confidence=1,
        needs_review=True,
        reason=reason,
        source="openai",
        model_used=settings.openai_classifier_model,
        cached=False,
        input_text=line,
    )


def predict_classification_with_openai(
    product_name: str | None,
    brand: str | None = None,
    db: Session | None = None,
    *,
    ingredients: list[Ingredient] | list[str] | None = None,
    nutrition: NutritionData | None = None,
    visual_labels: list[str] | None = None,
    visual_product_type: str | None = None,
) -> Optional[ClassifierPrediction]:
    """Classify a product via OpenAI with caching.

    The extra keyword-only parameters carry additional label evidence
    (ingredients, nutrition per 100 g/ml, visual labels, image-derived product
    type). They let the model resolve products whose name alone is ambiguous
    (e.g. ``"Orchid Valley Delight"`` -> uses ingredients + visual hint to
    decide it's a fruit juice drink).

    Returns ``None`` only when there is genuinely nothing to classify (no usable
    product name/brand) or the classifier is fully disabled. In every other
    failure mode (no API key, API error, model declined, empty label universe)
    we return a ``ClassifierPrediction`` with empty labels and a ``reason``
    describing why — this stays visible in the UI's runtime-classifier card.
    """
    if not settings.openai_classifier_enabled:
        logger.info("OpenAI classifier disabled by config; not running.")
        return None

    line = compose_product_query_text(product_name, brand) or ""
    if not line:
        return None

    cache_key = _build_cache_key(product_name, brand)
    if cache_key:
        cached = _read_cache(cache_key, db)
        if cached is not None:
            return cached

    classes, subclasses, novas = _load_label_universe(db)
    if not (classes or subclasses or novas):
        logger.warning(
            "Classifier label universe is empty (no rows in product_nutrition or DB "
            "unreachable); returning empty prediction."
        )
        return _empty_prediction(
            line,
            "Label universe unavailable (no rows in product_nutrition or DB unreachable).",
        )

    result = _call_openai(
        line=line,
        brand=brand,
        ingredients=ingredients,
        nutrition=nutrition,
        visual_labels=visual_labels,
        visual_product_type=visual_product_type,
        classes=classes,
        subclasses=subclasses,
        novas=novas,
        model=settings.openai_classifier_model,
        timeout_s=settings.openai_classifier_timeout_s,
    )
    if result is None:
        # _call_openai already logged the underlying cause (no API key, request
        # error, non-JSON response, ...). Surface it to the UI generically.
        return _empty_prediction(
            line,
            "OpenAI call did not return a usable response (see server logs).",
        )

    cls_s, sub_s, nova_s, conf_int, reason = result
    if not (cls_s or sub_s or nova_s):
        # Model legitimately declined. Do NOT cache (we'd cache emptiness forever).
        logger.info(
            "OpenAI classifier returned no labels for line=%r (model declined).",
            line[:80],
        )
        return _empty_prediction(
            line,
            reason or "Model declined: no allow-list label fit this product.",
        )

    threshold = max(1, min(5, int(settings.openai_classifier_review_threshold)))
    needs_review = conf_int < threshold

    pred = ClassifierPrediction(
        class_name=cls_s,
        subclass_name=sub_s,
        nova=nova_s,
        confidence=conf_int,
        needs_review=needs_review,
        reason=reason or None,
        source="openai",
        model_used=settings.openai_classifier_model,
        cached=False,
        input_text=line,
    )

    if cache_key:
        _write_cache(
            cache_key=cache_key,
            product_name=line,
            brand=brand,
            pred=pred,
            db=db,
        )
    return pred
