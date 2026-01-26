from __future__ import annotations

import io
import json
from functools import partial
from typing import Any, List

import anyio
import replicate

from app.config import settings
from app.models import (
    ExtractionMetadata,
    Ingredient,
    NutritionData,
    OcrResult,
    ProductInfo,
)


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


def _validate_nutrition_value(value: Any, field_name: str) -> float | None:
    """
    Validate and normalize a nutrition value.
    
    Returns None if value is invalid, otherwise returns the float value.
    """
    if value is None:
        return None
    
    try:
        float_value = float(value)
        # Basic validation: values should be non-negative and reasonable
        if float_value < 0:
            return None
        # Check for impossibly high values (e.g., >100g per 100g is impossible)
        if field_name in ["total_fat", "saturated_fat", "trans_fat", "protein", "carbohydrates", "fiber"]:
            if float_value > 100:
                return None
        return float_value
    except (ValueError, TypeError):
        return None


def _parse_nutrition_data(data: dict) -> NutritionData | None:
    """Parse nutrition data from the model's JSON response."""
    nutrition_dict = data.get("nutrition_per_100g")
    if not nutrition_dict or not isinstance(nutrition_dict, dict):
        return None
    
    # Parse core KNPM nutrients
    core_nutrients = {
        "energy_kcal": _validate_nutrition_value(nutrition_dict.get("energy_kcal"), "energy_kcal"),
        "total_fat": _validate_nutrition_value(nutrition_dict.get("total_fat"), "total_fat"),
        "saturated_fat": _validate_nutrition_value(nutrition_dict.get("saturated_fat"), "saturated_fat"),
        "trans_fat": _validate_nutrition_value(nutrition_dict.get("trans_fat"), "trans_fat"),
        "total_sugar": _validate_nutrition_value(nutrition_dict.get("total_sugar"), "total_sugar"),
        "sodium": _validate_nutrition_value(nutrition_dict.get("sodium"), "sodium"),
        "protein": _validate_nutrition_value(nutrition_dict.get("protein"), "protein"),
        "carbohydrates": _validate_nutrition_value(nutrition_dict.get("carbohydrates"), "carbohydrates"),
        "fiber": _validate_nutrition_value(nutrition_dict.get("fiber"), "fiber"),
    }
    
    # Parse additional nutrients (potassium, calcium, iron, vitamins, etc.)
    additional_nutrients_raw = nutrition_dict.get("additional_nutrients", {})
    additional_nutrients: dict[str, float] = {}
    
    if isinstance(additional_nutrients_raw, dict):
        for nutrient_name, value in additional_nutrients_raw.items():
            validated_value = _validate_nutrition_value(value, nutrient_name)
            if validated_value is not None:
                # Normalize nutrient name (lowercase, replace spaces with underscores)
                normalized_name = str(nutrient_name).lower().strip().replace(" ", "_")
                additional_nutrients[normalized_name] = validated_value
    
    return NutritionData(
        energy_kcal=core_nutrients["energy_kcal"],
        total_fat=core_nutrients["total_fat"],
        saturated_fat=core_nutrients["saturated_fat"],
        trans_fat=core_nutrients["trans_fat"],
        total_sugar=core_nutrients["total_sugar"],
        sodium=core_nutrients["sodium"],
        protein=core_nutrients["protein"],
        carbohydrates=core_nutrients["carbohydrates"],
        fiber=core_nutrients["fiber"],
        additional_nutrients=additional_nutrients,
    )


def _parse_product_info(data: dict) -> ProductInfo | None:
    """Parse product information from the model's JSON response."""
    product_dict = data.get("product_info")
    if not product_dict or not isinstance(product_dict, dict):
        return None
    
    return ProductInfo(
        name=product_dict.get("name") if product_dict.get("name") else None,
        brand=product_dict.get("brand") if product_dict.get("brand") else None,
        category=product_dict.get("category") if product_dict.get("category") else None,
        barcode=product_dict.get("barcode") if product_dict.get("barcode") else None,
    )


def _detect_trans_fats_and_sweeteners(ingredients: List[Ingredient]) -> tuple[bool, bool]:
    """
    Detect presence of trans fats and artificial sweeteners from ingredients list.
    
    Returns: (has_trans_fats, has_artificial_sweeteners)
    """
    trans_fat_keywords = [
        "trans fat", "trans-fat", "partially hydrogenated", "hydrogenated oil",
        "shortening", "margarine"
    ]
    
    artificial_sweetener_keywords = [
        "aspartame", "sucralose", "saccharin", "acesulfame", "stevia",
        "sorbitol", "xylitol", "erythritol", "monk fruit", "artificial sweetener",
        "non-nutritive sweetener", "nns"
    ]
    
    ingredient_text = " ".join([ing.name.lower() for ing in ingredients])
    
    has_trans_fats = any(keyword in ingredient_text for keyword in trans_fat_keywords)
    has_sweeteners = any(keyword in ingredient_text for keyword in artificial_sweetener_keywords)
    
    return has_trans_fats, has_sweeteners


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
    Core OCR entry point using GPT-4.1-mini via Replicate.

    - Accepts raw image bytes.
    - Sends them to the Replicate-hosted `openai/gpt-4.1-mini` model with the
      image passed as an `image_input` file.
    - Asks the model to return a strict JSON structure with ingredients,
      nutrition facts, product info, and extraction metadata.
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
        "You are an OCR and food label extraction engine. "
        "You receive an image of a food product label and must extract:\n"
        "1. Ingredients list (if visible)\n"
        "2. Nutrition facts table (if visible) - extract ALL nutrients visible in the table\n"
        "3. Product name, brand, category (if visible)\n"
        "4. Barcode (if visible)\n\n"
        "Return a single JSON object with these keys:\n"
        '   - \"ingredients\": array of strings (empty array if not found)\n'
        '   - \"nutrition_per_100g\": object with:\n'
        '     * Core nutrients (use null if NOT in the image): energy_kcal, total_fat, '
        'saturated_fat, trans_fat, total_sugar, sodium, protein, carbohydrates, fiber\n'
        '     * \"additional_nutrients\": object with ALL other nutrients found in the table '
        '(e.g., potassium, calcium, iron, vitamins, etc.) as key-value pairs\n'
        '       Example: {\"potassium\": 200, \"calcium\": 50, \"iron\": 2.5}\n'
        '   - \"product_info\": object with keys: name, brand, category, barcode (use null if not found)\n'
        '   - \"raw_text\": string with all text you read from the label\n'
        '   - \"extraction_metadata\": object with boolean keys: ingredients_found, '
        'nutrition_facts_found, product_name_found, barcode_found\n\n'
        "IMPORTANT:\n"
        "- Extract ALL nutrients visible in the nutrition facts table, not just the core ones\n"
        "- For core nutrients: use null ONLY if that nutrient is NOT in the image\n"
        "- For additional nutrients: include EVERY nutrient you see (potassium, calcium, iron, vitamins, etc.)\n"
        "- Extract nutrition values as numbers (grams per 100g/100ml, or as shown on label)\n"
        "- If a section is not visible, set the corresponding field to null or empty array\n"
        "- Set extraction_metadata flags to true only if you actually found that information\n"
        "- Return ONLY JSON, no explanations or commentary"
    )

    # Prepare image as a file-like object for Replicate.
    image_file = io.BytesIO(image_bytes)
    image_file.name = "label.jpg"

    # For OpenAI models hosted on Replicate we use `prompt` + `system_prompt`
    # rather than the `messages` array to avoid the "messages: empty array"
    # validation issues.
    inputs: dict[str, Any] = {
        "prompt": (
            "Extract all information from this food label image and return ONLY "
            "a JSON object with the keys 'ingredients', 'nutrition_per_100g', "
            "'product_info', 'raw_text', and 'extraction_metadata' as previously described."
        ),
        "system_prompt": system_prompt,
        "image_input": [image_file],
        "temperature": 0.1,
        "max_completion_tokens": 1024,  # Increased for nutrition facts
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
    if not cleaned_response:
        # If we can't parse anything, return minimal result with error
        return OcrResult(
            ingredients=[],
            nutrition_per_100g=None,
            product_info=None,
            raw_text=None,
            extraction_metadata=ExtractionMetadata(
                ingredients_found=False,
                nutrition_facts_found=False,
                product_name_found=False,
                barcode_found=False,
            ),
            warnings=[],
            errors=[
                "Could not extract any data from the image. Please ensure the label is clearly visible."
            ],
            model_raw_output={"output": text_response},
        )

    try:
        parsed = json.loads(cleaned_response)
    except json.JSONDecodeError:
        # If JSON parsing fails, return error
        return OcrResult(
            ingredients=[],
            nutrition_per_100g=None,
            product_info=None,
            raw_text=None,
            extraction_metadata=ExtractionMetadata(
                ingredients_found=False,
                nutrition_facts_found=False,
                product_name_found=False,
                barcode_found=False,
            ),
            warnings=[],
            errors=["Failed to parse model response as JSON"],
            model_raw_output={"output": text_response},
        )

    # Parse all fields
    ingredients = _parse_ingredients_from_model_text(text_response)
    nutrition_data = _parse_nutrition_data(parsed)
    product_info = _parse_product_info(parsed)
    
    # Get extraction metadata
    metadata_dict = parsed.get("extraction_metadata", {})
    extraction_metadata = ExtractionMetadata(
        ingredients_found=metadata_dict.get("ingredients_found", len(ingredients) > 0),
        nutrition_facts_found=metadata_dict.get("nutrition_facts_found", nutrition_data is not None),
        product_name_found=metadata_dict.get("product_name_found", product_info is not None and product_info.name is not None),
        barcode_found=metadata_dict.get("barcode_found", product_info is not None and product_info.barcode is not None),
    )
    
    # Get raw text
    raw_text_value = parsed.get("raw_text") if isinstance(parsed.get("raw_text"), str) else None
    
    # Generate warnings and errors
    warnings: List[str] = []
    errors: List[str] = []
    
    if not extraction_metadata.ingredients_found:
        warnings.append("Ingredients list not found in image")
    
    if not extraction_metadata.nutrition_facts_found:
        warnings.append("Nutrition facts table not found in image")
        errors.append("Cannot perform KNPM labeling - nutrition data required")
    
    if not extraction_metadata.product_name_found:
        warnings.append("Product name not found in image")
    
    # If both ingredients and nutrition are missing, add error
    if not extraction_metadata.ingredients_found and not extraction_metadata.nutrition_facts_found:
        errors.append("Insufficient data extracted - both ingredients and nutrition facts are missing")
        if not raw_text_value:
            errors.append("No readable text found in image")
            warnings.append("Please ensure the label is clearly visible and in focus")
            warnings.append("Try taking a photo with better lighting")
    
    # Detect trans fats and artificial sweeteners from ingredients
    if ingredients:
        has_trans_fats, has_sweeteners = _detect_trans_fats_and_sweeteners(ingredients)
        if has_trans_fats and nutrition_data:
            # Update trans_fat if detected but not in nutrition data
            if nutrition_data.trans_fat is None or nutrition_data.trans_fat == 0:
                # Note: We can't set a value without knowing the amount, but we can add a warning
                warnings.append("Trans fats detected in ingredients list")
        if has_sweeteners:
            warnings.append("Artificial sweeteners detected in ingredients list")

    return OcrResult(
        ingredients=ingredients,
        nutrition_per_100g=nutrition_data,
        product_info=product_info,
        raw_text=raw_text_value,
        extraction_metadata=extraction_metadata,
        warnings=warnings,
        errors=errors,
        model_raw_output={"output": text_response},
    )



