"""
Optional GenAI copy for healthier substitutes (OpenAI chat).

Falls back to a deterministic template when the API key is missing or the call fails.
"""

from __future__ import annotations

import logging
import anyio
from openai import OpenAI

from app.config import settings
from app.models import HealthierSubstituteResult, OcrResult
from app.services.healthier_substitutes import template_explanation

logger = logging.getLogger(__name__)


def _build_prompt(ocr: OcrResult, result: HealthierSubstituteResult, user_goal: str | None) -> str:
    knpm = ocr.knpm_label
    octs = list(knpm.octagons) if knpm else []
    reasons = list(knpm.reasons) if knpm else []
    subs_lines: list[str] = []
    for s in result.substitutes:
        subs_lines.append(
            f"- {s.product_name} (tier {s.tier}, octagons={s.octagon_count}, "
            f"below_threshold={s.below_knpm_thresholds})"
        )
    goal_line = (
        f"Optional shopper goal (often unused): {user_goal.strip()}\n"
        if user_goal and user_goal.strip()
        else ""
    )
    exceeded = ", ".join(result.exceeded_nutrient_summary) or "see KNPM reasons"
    widen = (
        "Note: search was widened beyond the closest subclass."
        if result.no_close_substitutes
        else ""
    )
    return f"""You are a concise nutrition assistant for a Kenyan retail / KNPM context.

{goal_line}The scanned product triggered concerns. KNPM-style warnings (octagons): {octs}.
Regulatory-style reasons (short): {reasons[:6]}
Exceeded nutrient tags: {exceeded}.
{widen}

Suggested substitutes from our reference database:
{chr(10).join(subs_lines) if subs_lines else "(none listed)"}

Write 2–4 short sentences for the shopper:
1) Which nutrients or conditions were problematic (plain language).
2) Why the listed alternatives are better (fewer or no black octagons under the same category limits).
3) If an optional shopper goal was given above, tie one sentence to it; otherwise omit.
Do not claim medical cures. Do not invent products not listed."""


async def generate_substitute_explanation_openai(
    ocr: OcrResult,
    result: HealthierSubstituteResult,
    user_goal: str | None,
) -> str:
    if not settings.openai_api_key:
        return template_explanation(ocr, result)

    client = OpenAI(api_key=settings.openai_api_key)
    prompt = _build_prompt(ocr, result, user_goal)

    def _call() -> str:
        r = client.chat.completions.create(
            model=settings.openai_model,
            temperature=0.3,
            max_tokens=280,
            messages=[
                {
                    "role": "system",
                    "content": "You write clear, brief shopper-facing nutrition guidance.",
                },
                {"role": "user", "content": prompt},
            ],
        )
        return (r.choices[0].message.content or "").strip()

    try:
        return await anyio.to_thread.run_sync(_call)
    except Exception as e:  # pragma: no cover - network
        logger.warning("OpenAI substitute explanation failed: %s", e)
        return template_explanation(ocr, result)


async def attach_healthier_recommendations(
    ocr: OcrResult,
    *,
    has_trans_fats: bool,
    has_sweeteners: bool,
    user_goal: str | None = None,
) -> OcrResult:
    if not settings.substitute_recommendations_enabled:
        return ocr

    from app.services.healthier_substitutes import build_healthier_substitutes

    sub = build_healthier_substitutes(
        ocr,
        has_trans_fats=has_trans_fats,
        has_sweeteners=has_sweeteners,
    )
    if sub is None:
        return ocr

    explanation: str | None = None
    if sub.triggered and sub.substitutes:
        if settings.substitute_explanation_enabled:
            explanation = await generate_substitute_explanation_openai(ocr, sub, user_goal)
        else:
            explanation = template_explanation(ocr, sub)
    elif sub.triggered:
        explanation = template_explanation(ocr, sub)

    updated = sub.model_copy(update={"explanation": explanation})
    return ocr.model_copy(update={"healthier_substitutes": updated})
