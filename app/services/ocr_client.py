from __future__ import annotations

import io
import json
from functools import partial
from typing import Any, List

import anyio
import replicate

from app.config import settings
from app.models import Ingredient, OcrResult


class OcrClientError(Exception):
    """Raised when the OCR client cannot process an image."""


def _clean_response_text(text: str) -> str:
    """
    Clean the model's response text to extract just the JSON portion.
    
    Removes markdown code fences, trailing commentary, and other noise
    that models sometimes add around JSON.
    """
    text = text.strip()
    if not text:
        return ""
    
    # Remove markdown code fences if present
    if text.startswith("```json"):
        text = text[7:]  # Remove ```json
    elif text.startswith("```"):
        text = text[3:]  # Remove ```
    if text.endswith("```"):
        text = text[:-3]  # Remove closing ```
    
    text = text.strip()
    
    # Try to find the JSON object boundaries
    json_start = text.find("{")
    json_end = text.rfind("}")
    
    if json_start != -1 and json_end != -1 and json_end > json_start:
        return text[json_start : json_end + 1]
    
    return text


def _parse_ingredients_from_model_text(text: str) -> List[Ingredient]:
    """
    Attempt to parse the model's response into a list of Ingredient objects.

    We instruct the model to return JSON, but this function is defensive and
    will try to cope with slight deviations (e.g., extra text around JSON).
    """
    cleaned = _clean_response_text(text)
    if not cleaned:
        return []

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # If JSON parsing fails, return empty list (don't fallback to raw text)
        return []

    ingredients_field = data.get("ingredients")
    if not isinstance(ingredients_field, list):
        return []

    ingredients: List[Ingredient] = []
    for item in ingredients_field:
        if isinstance(item, str):
            ingredients.append(Ingredient(name=item.strip()))
        elif isinstance(item, dict) and "name" in item:
            ingredients.append(
                Ingredient(
                    name=str(item.get("name", "")).strip(),
                )
            )

    return ingredients


async def extract_ingredients_from_image(image_bytes: bytes) -> OcrResult:
    """
    Core OCR entry point using GPT-4o mini via Replicate.

    - Accepts raw image bytes.
    - Sends them to the Replicate-hosted `openai/gpt-4o-mini` model with the
      image passed as an `image_input` file.
    - Asks the model to return a strict JSON structure with an `ingredients`
      list and an optional `raw_text` field.
    - Parses the response into our internal `OcrResult` model.
    """
    if not image_bytes:
        raise OcrClientError("Empty image payload")

    if not settings.replicate_api_token:
        raise OcrClientError(
            "REPLICATE_API_TOKEN is not set in the environment. "
            "Please add it to your .env file."
        )

    system_prompt = (
        "You are an OCR and ingredients extraction engine for food labels. "
        "You receive an image of a product label and must:\n"
        "1. Read any visible ingredients list.\n"
        "2. Return a single JSON object with the following keys:\n"
        '   - \"ingredients\": an array of strings, each a clean ingredient name in order.\n'
        '   - \"raw_text\": a single string with the raw text you read from the label.\n'
        "Do not include any other keys. Do not include explanations or commentary. "
        "Return only JSON."
    )

    # Prepare image as a file-like object for Replicate.
    image_file = io.BytesIO(image_bytes)
    image_file.name = "label.jpg"

    # For OpenAI models hosted on Replicate we use `prompt` + `system_prompt`
    # rather than the `messages` array to avoid the "messages: empty array"
    # validation issues.
    inputs: dict[str, Any] = {
        "prompt": (
            "Extract the ingredients from this food label image and return ONLY "
            "a JSON object with the keys 'ingredients' and 'raw_text' as "
            "previously described."
        ),
        "system_prompt": system_prompt,
        "image_input": [image_file],
        "temperature": 0.1,
        "max_completion_tokens": 512,
    }

    model_identifier = settings.replicate_model

    # Initialize Replicate client with API token
    client = replicate.Client(api_token=settings.replicate_api_token)

    # Replicate's Python client is synchronous; run it in a worker thread so
    # we do not block the event loop.
    # Use functools.partial to bind the arguments since run_sync doesn't accept kwargs directly
    try:
        run_model = partial(client.run, model_identifier, input=inputs)
        output = await anyio.to_thread.run_sync(run_model)
    except Exception as exc:  # pragma: no cover - defensive
        raise OcrClientError(f"Error calling Replicate API: {exc}") from exc

    # Replicate chat models typically return either a string or a list of
    # string chunks. Normalise to a single text string.
    if isinstance(output, str):
        text_response = output
    elif isinstance(output, list):
        text_response = "".join(str(part) for part in output)
    else:
        text_response = str(output)

    # Clean and parse the response
    cleaned_response = _clean_response_text(text_response)
    ingredients = _parse_ingredients_from_model_text(text_response)

    raw_text_value: str | None = None
    # If the JSON contained a raw_text field, try to capture it.
    if cleaned_response:
        try:
            parsed = json.loads(cleaned_response)
            if isinstance(parsed, dict) and isinstance(parsed.get("raw_text"), str):
                raw_text_value = parsed["raw_text"]
        except json.JSONDecodeError:
            raw_text_value = None

    return OcrResult(
        ingredients=ingredients,
        raw_text=raw_text_value,
        model_raw_output={"output": text_response},
    )



