from __future__ import annotations

import json
import base64
from typing import Any, List

import anyio
from openai import OpenAI

from app.config import settings
from app.models import (
    ExtractionMetadata,
    Ingredient,
    NutritionData,
    OcrResult,
    ProductInfo,
)
from app.services.classification_consistency import (
    warning_pos_taxonomy_vs_label_sugar,
)
from app.services.knpm_category_thresholds import (
    resolve_knpm_thresholds_for_extraction,
)
from app.services.foodclasses_bilstm_inference import (
    merge_foodclasses_with_pos,
    predict_foodclasses_from_product_text,
)
from app.services.knpm_labeller import classify_with_knpm
from app.services.nova_bilstm_inference import (
    maybe_fill_supermarket_nova,
    predict_nova_from_product_text,
)
from app.services.reference_nutrition_lookup import lookup_reference_nutrition
from app.services.recommendation_explainer import attach_healthier_recommendations
from app.services.supermarket_lookup import lookup_supermarket_classification


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


def _nutrition_has_numeric_values(nutrition: NutritionData | None) -> bool:
    """True if any per-100g number is present (same idea as KNPM labeller)."""
    if nutrition is None:
        return False
    if nutrition.additional_nutrients:
        return True
    return any(
        getattr(nutrition, field) is not None
        for field in (
            "energy_kcal",
            "total_fat",
            "saturated_fat",
            "trans_fat",
            "total_sugar",
            "sodium",
            "protein",
            "carbohydrates",
            "fiber",
        )
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _parse_product_info(data: dict) -> ProductInfo | None:
    """Parse product information from the model's JSON response."""
    product_dict = data.get("product_info")
    if not product_dict or not isinstance(product_dict, dict):
        return None

    return ProductInfo(
        name=_optional_str(product_dict.get("name")),
        brand=_optional_str(product_dict.get("brand")),
        category=_optional_str(product_dict.get("category")),
        visual_product_type=_optional_str(product_dict.get("visual_product_type")),
        barcode=_optional_str(product_dict.get("barcode")),
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


async def extract_ingredients_from_image(
    image_bytes: bytes,
    user_goal: str | None = None,
) -> OcrResult:
    """
    Core OCR entry point using GPT-4.1-mini via OpenAI directly.

    - Accepts raw image bytes.
    - Sends them to OpenAI's vision-capable GPT-4.1-mini model with the image
      passed as an inline base64 data URL.
    - Asks the model to return a strict JSON structure with ingredients,
      nutrition facts, product info, and extraction metadata.
    - Parses the response into our internal `OcrResult` model.
    """
    if not image_bytes:
        raise OcrClientError("Empty image payload")

    if not settings.openai_api_key:
        raise OcrClientError(
            "OPENAI_API_KEY is not set in the environment. "
            "Please add it to your .env file."
        )

    system_prompt = (
        "You are an OCR and food label extraction engine. "
        "You receive an image of a food product label and must extract:\n"
        "1. Ingredients list (if visible)\n"
        "2. Nutrition facts table (if visible) - extract ALL nutrients visible in the table\n"
        "3. Product name, brand, category from printed text (if visible)\n"
        "4. Barcode (if visible)\n"
        "5. Visual product type: infer from the **whole image** (pack shape, photos, logos, "
        "layout, colours) what the product **is** in plain English when text does not give a "
        "clear category, or to add detail (e.g. \"orange juice drink\", \"instant noodle cup\", "
        "\"chocolate wafer\"). Do NOT invent retailer/POS class codes. Use null if you cannot "
        "reasonably infer it.\n\n"
        "Return a single JSON object with these keys:\n"
        '   - \"ingredients\": array of strings (empty array if not found)\n'
        '   - \"nutrition_per_100g\": object with:\n'
        '     * Core nutrients (use null if NOT in the image): energy_kcal, total_fat, '
        'saturated_fat, trans_fat, total_sugar, sodium, protein, carbohydrates, fiber\n'
        '     * \"additional_nutrients\": object with ALL other nutrients found in the table '
        '(e.g., potassium, calcium, iron, vitamins, etc.) as key-value pairs\n'
        '       Example: {\"potassium\": 200, \"calcium\": 50, \"iron\": 2.5}\n'
        '   - \"product_info\": object with keys: name, brand, category, barcode, '
        'visual_product_type (use null for any unknown field; always include all keys)\n'
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

    # Encode image as base64 data URL for OpenAI vision model
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    image_data_url = f"data:image/jpeg;base64,{image_b64}"

    user_prompt = (
        "Extract all information from this food label image and return ONLY "
        "a JSON object with the keys 'ingredients', 'nutrition_per_100g', "
        "'product_info', 'raw_text', and 'extraction_metadata' as previously described."
    )

    client = OpenAI(api_key=settings.openai_api_key)

    async def _run_openai() -> str:
        # OpenAI client is synchronous; run it in a worker thread
        def _call() -> str:
            response = client.chat.completions.create(
                model=settings.openai_model,
                temperature=0.1,
                max_tokens=1280,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": user_prompt},
                            {
                                "type": "image_url",
                                "image_url": {"url": image_data_url},
                            },
                        ],
                    },
                ],
            )
            # We expect a single text choice
            return response.choices[0].message.content or ""

        return await anyio.to_thread.run_sync(_call)

    try:
        text_response = await _run_openai()
    except Exception as exc:  # pragma: no cover - defensive
        raise OcrClientError(f"Error calling OpenAI API: {exc}") from exc

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
    label_nutrition = _parse_nutrition_data(parsed)
    nutrition_data = label_nutrition
    product_info = _parse_product_info(parsed)

    reference_nutrition_match = None
    nutrition_from_ref = False
    if settings.reference_nutrition_lookup_enabled and not _nutrition_has_numeric_values(
        nutrition_data
    ):
        ref_nut, ref_meta = lookup_reference_nutrition(product_info)
        if ref_nut is not None and ref_meta is not None:
            nutrition_data = ref_nut
            reference_nutrition_match = ref_meta
            nutrition_from_ref = True

    # Get extraction metadata
    metadata_dict = parsed.get("extraction_metadata", {})
    label_nutrition_present = label_nutrition is not None
    extraction_metadata = ExtractionMetadata(
        ingredients_found=metadata_dict.get("ingredients_found", len(ingredients) > 0),
        nutrition_facts_found=metadata_dict.get(
            "nutrition_facts_found", label_nutrition_present
        ),
        product_name_found=metadata_dict.get(
            "product_name_found",
            product_info is not None and product_info.name is not None,
        ),
        barcode_found=metadata_dict.get(
            "barcode_found",
            product_info is not None and product_info.barcode is not None,
        ),
        nutrition_from_reference_lookup=nutrition_from_ref,
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

    if nutrition_from_ref:
        warnings.append(
            "Nutrition per 100 g/ml was filled from the in-app reference database "
            "(product name match), not read from the label image."
        )

    if not _nutrition_has_numeric_values(nutrition_data):
        errors.append("Cannot perform KNPM labeling - nutrition data required")

    if not extraction_metadata.product_name_found:
        warnings.append("Product name not found in image")

    # If both ingredients and usable nutrition are missing, add error
    if not extraction_metadata.ingredients_found and not _nutrition_has_numeric_values(
        nutrition_data
    ):
        errors.append("Insufficient data extracted - both ingredients and nutrition facts are missing")
        if not raw_text_value:
            errors.append("No readable text found in image")
            warnings.append("Please ensure the label is clearly visible and in focus")
            warnings.append("Try taking a photo with better lighting")
    
    # Detect trans fats and artificial sweeteners from ingredients
    has_trans_fats = False
    has_sweeteners = False
    if ingredients:
        has_trans_fats, has_sweeteners = _detect_trans_fats_and_sweeteners(ingredients)
        if has_trans_fats and nutrition_data:
            # Update trans_fat if detected but not in nutrition data
            if nutrition_data.trans_fat is None or nutrition_data.trans_fat == 0:
                # Note: We can't set a value without knowing the amount, but we can add a warning
                warnings.append("Trans fats detected in ingredients list")
        if has_sweeteners:
            warnings.append("Artificial sweeteners detected in ingredients list")

    # POS taxonomy first so KNPM can use subclass/class text in category-threshold hints.
    supermarket_classification = lookup_supermarket_classification(product_info)
    foodclasses_bilstm_prediction = None
    if settings.foodclasses_bilstm_enabled and product_info is not None:
        foodclasses_bilstm_prediction = predict_foodclasses_from_product_text(
            product_info.name,
            product_info.brand,
        )
        if foodclasses_bilstm_prediction is not None:
            c_ok = (foodclasses_bilstm_prediction.class_confidence or 0.0) >= float(
                settings.foodclasses_bilstm_min_class_confidence
            )
            s_ok = (foodclasses_bilstm_prediction.subclass_confidence or 0.0) >= float(
                settings.foodclasses_bilstm_min_subclass_confidence
            )
            if not c_ok or not s_ok:
                warnings.append(
                    "Foodclasses BiLSTM confidence below threshold; keeping POS taxonomy "
                    f"(class={foodclasses_bilstm_prediction.class_confidence:.3f}, "
                    f"subclass={foodclasses_bilstm_prediction.subclass_confidence:.3f})."
                )
        supermarket_classification = merge_foodclasses_with_pos(
            supermarket_classification,
            foodclasses_bilstm_prediction,
        )

    nova_bilstm_prediction = None
    if settings.nova_bilstm_enabled and product_info is not None:
        nova_bilstm_prediction = predict_nova_from_product_text(
            product_info.name,
            product_info.brand,
        )
        supermarket_classification = maybe_fill_supermarket_nova(
            supermarket_classification,
            nova_bilstm_prediction,
        )

    threshold_row, thr_source, thr_score = resolve_knpm_thresholds_for_extraction(
        product_info,
        supermarket_classification,
    )
    if thr_source == "csv_default_composite":
        warnings.append(
            "KNPM nutrient limits used: category 6.0 (Composite foods) — no specific "
            "food category matched from label/POS hints; thresholds may be stricter or looser "
            "than the true KNPM category."
        )
    elif thr_source == "csv_pos_class_bridge":
        warnings.append(
            "KNPM category limits were chosen from retailer POS class → KNPM mapping "
            "(fuzzy match to MoH category names was inconclusive). "
            "Review `knpm_category_number` in the response."
        )

    knpm_label = classify_with_knpm(
        nutrition=nutrition_data,
        has_trans_fats=has_trans_fats if ingredients else False,
        has_sweeteners=has_sweeteners if ingredients else False,
        threshold_row=threshold_row,
        thresholds_source=thr_source,
        category_match_score=thr_score,
    )

    # If we could not classify due to missing nutrition facts, surface that message as a warning.
    if knpm_label.message:
        warnings.append(knpm_label.message)

    pos_sugar_mismatch = warning_pos_taxonomy_vs_label_sugar(
        knpm_label, supermarket_classification
    )
    if pos_sugar_mismatch:
        warnings.append(pos_sugar_mismatch)

    model_raw: dict[str, Any] = {"output": text_response}
    if reference_nutrition_match is not None:
        model_raw["reference_nutrition_match"] = (
            reference_nutrition_match.model_dump()
        )
    if nova_bilstm_prediction is not None:
        model_raw["nova_bilstm_prediction"] = nova_bilstm_prediction.model_dump()
    if foodclasses_bilstm_prediction is not None:
        model_raw["foodclasses_bilstm_prediction"] = (
            foodclasses_bilstm_prediction.model_dump()
        )

    result = OcrResult(
        ingredients=ingredients,
        nutrition_per_100g=nutrition_data,
        product_info=product_info,
        raw_text=raw_text_value,
        extraction_metadata=extraction_metadata,
        warnings=warnings,
        errors=errors,
        model_raw_output=model_raw,
        knpm_label=knpm_label,
        supermarket_classification=supermarket_classification,
        reference_nutrition_match=reference_nutrition_match,
        nova_bilstm_prediction=nova_bilstm_prediction,
        foodclasses_bilstm_prediction=foodclasses_bilstm_prediction,
    )
    return await attach_healthier_recommendations(
        result,
        has_trans_fats=has_trans_fats if ingredients else False,
        has_sweeteners=has_sweeteners if ingredients else False,
        user_goal=user_goal,
    )



