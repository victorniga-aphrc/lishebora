from __future__ import annotations

import json
import base64
from typing import Any, List

import anyio
from openai import OpenAI
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import (
    ExtractedData,
    ExtractionMetadata,
    Ingredient,
    NutritionData,
    NutritionResolution,
    OcrResult,
    ParsedData,
    ProductNutritionMatchMetadata,
    ProductInfo,
)
from app.services.foodclasses_bilstm_inference import (
    is_strong_catalog_classification,
    merge_foodclasses_with_classification,
    predict_foodclasses_from_product_text,
)
from app.services.knpm_labeller import classify_with_knpm
from app.services.reference_catalog_db import (
    lookup_product_classification_db,
    lookup_reference_nutrition_db,
)
from app.services.recommendation_explainer import attach_healthier_recommendations


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
        if field_name in ["total_fat", "trans_fat", "total_sugar"]:
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
    
    # Parse pipeline-active nutrients only
    core_nutrients = {
        "total_fat": _validate_nutrition_value(nutrition_dict.get("total_fat"), "total_fat"),
        "trans_fat": _validate_nutrition_value(nutrition_dict.get("trans_fat"), "trans_fat"),
        "total_sugar": _validate_nutrition_value(nutrition_dict.get("total_sugar"), "total_sugar"),
        "sodium": _validate_nutrition_value(nutrition_dict.get("sodium"), "sodium"),
    }

    return NutritionData(
        total_fat=core_nutrients["total_fat"],
        trans_fat=core_nutrients["trans_fat"],
        total_sugar=core_nutrients["total_sugar"],
        sodium=core_nutrients["sodium"],
    )


def is_usable_nutrition(nutrition: NutritionData | None) -> bool:
    """True when at least one pipeline nutrient has a numeric value."""
    if nutrition is None:
        return False
    return any(
        getattr(nutrition, field) is not None
        for field in (
            "total_fat",
            "trans_fat",
            "total_sugar",
            "sodium",
        )
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s or None


def _optional_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "yes", "1"}:
            return True
        if lowered in {"false", "no", "0"}:
            return False
    return None


def _coerce_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            text = _optional_str(item)
            if text:
                out.append(text)
        return out
    text = _optional_str(value)
    return [text] if text else []


def _coerce_confidence_map(value: Any) -> dict[str, float]:
    if isinstance(value, (int, float)):
        return {"overall": float(value)}
    if isinstance(value, str):
        try:
            return {"overall": float(value.strip())}
        except ValueError:
            return {}
    if not isinstance(value, dict):
        return {}
    out: dict[str, float] = {}
    for key, raw in value.items():
        try:
            out[str(key)] = float(raw)
        except (TypeError, ValueError):
            continue
    return out


def _build_extracted_data(
    text_response: str,
    parsed_json: dict[str, Any] | None,
    parse_error: str | None = None,
) -> ExtractedData:
    parsed = parsed_json or {}
    product_info = parsed.get("product_info") if isinstance(parsed.get("product_info"), dict) else {}
    visual = parsed.get("visual_analysis") if isinstance(parsed.get("visual_analysis"), dict) else {}
    raw_text = _optional_str(parsed.get("raw_text"))
    raw_ingredients_text = _optional_str(parsed.get("raw_ingredients_text"))
    raw_nutrition_table_text = _optional_str(parsed.get("raw_nutrition_table_text"))
    raw_front_text = _optional_str(parsed.get("raw_front_text")) or raw_text
    detected_barcodes = _coerce_str_list(parsed.get("detected_barcodes"))
    barcode = _optional_str(product_info.get("barcode"))
    if barcode and barcode not in detected_barcodes:
        detected_barcodes.append(barcode)
    detected_logos = _coerce_str_list(parsed.get("detected_logos"))
    brand = _optional_str(product_info.get("brand"))
    if brand and brand not in detected_logos:
        detected_logos.append(brand)
    visual_labels = _coerce_str_list(visual.get("labels"))
    visual_product_type = _optional_str(product_info.get("visual_product_type"))
    category = _optional_str(product_info.get("category"))
    if not visual_labels and visual_product_type:
        visual_labels = [visual_product_type]
    elif not visual_labels and category:
        visual_labels = [category]
    return ExtractedData(
        raw_response_text=text_response or None,
        parsed_json=parsed_json,
        raw_front_text=raw_front_text,
        raw_ingredients_text=raw_ingredients_text,
        raw_nutrition_table_text=raw_nutrition_table_text,
        detected_barcodes=detected_barcodes,
        detected_logos=detected_logos,
        visual_is_food=_optional_bool(visual.get("is_food")),
        visual_is_packaged_retail_food=_optional_bool(
            visual.get("is_packaged_retail_food")
        ),
        visual_labels=visual_labels,
        visual_confidence=_coerce_confidence_map(visual.get("confidence")),
        visual_notes=_optional_str(visual.get("notes")),
        parse_error=parse_error,
    )


def _parse_product_info(data: dict) -> ProductInfo | None:
    """Parse product information from the model's JSON response."""
    product_dict = data.get("product_info")
    if not product_dict or not isinstance(product_dict, dict):
        return None

    name = _optional_str(product_dict.get("name"))
    visual_product_type = _optional_str(product_dict.get("visual_product_type"))

    return ProductInfo(
        name=name or visual_product_type,
        brand=_optional_str(product_dict.get("brand")),
        category=_optional_str(product_dict.get("category")),
        visual_product_type=visual_product_type,
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


def parse_extracted_data(extracted_data: ExtractedData) -> ParsedData:
    """Convert grouped raw extraction output into structured parsed data."""
    parsed_json = extracted_data.parsed_json or {}
    product_info = _parse_product_info(parsed_json)
    visual_labels = extracted_data.visual_labels

    # For text-light images, use a visual label as fallback name so downstream DB lookup has a query string.
    # Unpackaged retail scenes should be stopped in _process_food_product_with_db before heavy steps.
    if product_info is None and visual_labels:
        product_info = ProductInfo(name=visual_labels[0], visual_product_type=visual_labels[0])
    elif product_info is not None and not product_info.name and visual_labels:
        product_info.name = visual_labels[0]

    return ParsedData(
        ingredients=_parse_ingredients_from_model_text(
            extracted_data.raw_response_text or ""
        ),
        nutrition=_parse_nutrition_data(parsed_json),
        product_info=product_info,
        visual_is_food=extracted_data.visual_is_food,
        visual_is_packaged_retail_food=extracted_data.visual_is_packaged_retail_food,
        visual_labels=visual_labels,
    )


def resolve_nutrition_data(
    parsed_data: ParsedData,
    db: Session | None,
) -> NutritionResolution:
    """Resolve nutrition from label first, then product DB."""
    nutrition_data = parsed_data.nutrition
    product_nutrition_match = None
    nutrition_source = "unavailable"
    lookup_error = None

    if is_usable_nutrition(nutrition_data):
        nutrition_source = "image"
    else:
        db_nut, db_meta = lookup_reference_nutrition_db(parsed_data.product_info, db)
        if db_nut is not None and db_meta is not None:
            nutrition_data = db_nut
            product_nutrition_match = ProductNutritionMatchMetadata(
                row_id=None,
                match_method=db_meta.match_method,
                matched_product_name=db_meta.matched_product_name,
                match_score=db_meta.match_score,
                sub_type=db_meta.sub_type,
                form=db_meta.form,
            )
            nutrition_source = settings.reference_catalog_source_label

    return NutritionResolution(
        nutrition_data=nutrition_data,
        nutrition_source=nutrition_source,
        product_nutrition_match=product_nutrition_match,
        lookup_error=lookup_error,
    )


async def process_food_image(
    image_bytes: bytes,
) -> ExtractedData:
    """
    Core OCR entry point using GPT-4.1-mini via OpenAI directly.

    - Accepts raw image bytes.
    - Sends them to OpenAI's vision-capable GPT-4.1-mini model with the image
      passed as an inline base64 data URL.
    - Asks the model to return a strict JSON structure with grouped raw text,
      visual cues, and parseable extraction fields.
    - Returns grouped raw extraction output for later pipeline steps.
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
        "You receive an image that may show an edible item or a non-food item. "
        "For this task, food means any edible item, including packaged foods, drinks, "
        "spices, condiments, seasonings, and cooking ingredients. You must extract:\n"
        "1. Ingredients list (if visible)\n"
        "2. Nutrition facts table (if visible) - extract required pipeline nutrients\n"
        "3. Product name, brand, category from printed text (if visible)\n"
        "4. Barcode (if visible)\n"
        "5. Visual product type: infer from the **whole image** (pack shape, photos, logos, "
        "layout, colours) what the product **is** in plain English when text does not give a "
        "clear category, or to add detail (e.g. \"orange juice drink\", \"instant noodle cup\", "
        "\"chocolate wafer\"). Do NOT invent internal taxonomy codes. Use null if you cannot "
        "reasonably infer it.\n\n"
        "Return a single JSON object with these keys:\n"
        '   - \"ingredients\": array of strings (empty array if not found)\n'
        '   - \"nutrition_per_100g\": object with keys: total_fat, '
        'trans_fat, total_sugar, sodium (use null if NOT in the image)\n'
        '   - \"product_info\": object with keys: name, brand, category, barcode, '
        'visual_product_type (use null for any unknown field; always include all keys)\n'
        '   - \"raw_text\": string with all text you read from the label\n'
        '   - \"raw_front_text\": string with front-of-pack / main visible text\n'
        '   - \"raw_ingredients_text\": string with ingredients text\n'
        '   - \"raw_nutrition_table_text\": string with nutrition table text\n'
        '   - \"detected_barcodes\": array of strings\n'
        '   - \"detected_logos\": array of strings (brand or logo hints)\n'
        '   - \"visual_analysis\": object with keys: is_food, is_packaged_retail_food, labels, confidence, notes\n'
        '   - \"extraction_metadata\": object with boolean keys: ingredients_found, '
        'nutrition_facts_found, product_name_found, barcode_found\n\n'
        "IMPORTANT:\n"
        "- Extract only these nutrition keys: total_fat, trans_fat, total_sugar, sodium\n"
        "- For each key above: use null ONLY if that nutrient is NOT in the image\n"
        "- Extract nutrition values as numbers (grams per 100g/100ml, or as shown on label)\n"
        "- Set visual_analysis.labels to at least one plain-English item/type when you can visually identify the object or product\n"
        "- Set visual_analysis.confidence as either a single number (overall confidence) or an object of named confidence scores\n"
        "- If the image is clearly non-food, set visual_analysis.is_food to false and explain briefly in visual_analysis.notes\n"
        "- If the image shows an edible item such as spices, condiments, or cooking ingredients, set visual_analysis.is_food to true\n"
        "- Set visual_analysis.is_packaged_retail_food to true only when the photo shows a retail packaged product "
        "with consumer packaging and/or printed label areas (bottle, jar, can, carton, bag, box, shrink-wrapped multipack, etc.). "
        "Set it to false for loose/unpackaged food (e.g. single tomatoes, bulk produce, deli meat on a tray without branded retail sleeve, "
        "restaurant plates, home-cooked meals, or unpackaged ingredients). Use null only if you truly cannot tell.\n"
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
        "'product_info', 'raw_text', 'raw_front_text', 'raw_ingredients_text', "
        "'raw_nutrition_table_text', 'detected_barcodes', 'detected_logos', "
        "'visual_analysis', and 'extraction_metadata' as previously described."
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
        return _build_extracted_data(
            text_response=text_response,
            parsed_json=None,
            parse_error="Could not extract any data from the image. Please ensure the label is clearly visible.",
        )

    try:
        parsed = json.loads(cleaned_response)
    except json.JSONDecodeError:
        return _build_extracted_data(
            text_response=text_response,
            parsed_json=None,
            parse_error="Failed to parse model response as JSON",
        )

    return _build_extracted_data(
        text_response=text_response,
        parsed_json=parsed,
    )


async def process_food_product(
    image_bytes: bytes,
    user_goal: str | None = None,
    db: Session | None = None,
) -> OcrResult:
    local_db = None
    if db is None:
        local_db = SessionLocal()
        db = local_db

    try:
        return await _process_food_product_with_db(
            image_bytes=image_bytes,
            user_goal=user_goal,
            db=db,
        )
    finally:
        if local_db is not None:
            local_db.close()


async def _process_food_product_with_db(
    image_bytes: bytes,
    user_goal: str | None,
    db: Session | None,
) -> OcrResult:
    extracted_data = await process_food_image(
        image_bytes=image_bytes,
    )
    if extracted_data.parsed_json is None:
        return OcrResult(
            ingredients=[],
            nutrition_per_100g=None,
            product_info=None,
            visual_is_food=None,
            visual_is_packaged_retail_food=None,
            visual_labels=[],
            parse_error=extracted_data.parse_error,
            raw_text=None,
            extraction_metadata=ExtractionMetadata(
                ingredients_found=False,
                nutrition_facts_found=False,
                product_name_found=False,
                barcode_found=False,
            ),
            warnings=[],
            errors=[extracted_data.parse_error or "Failed to parse model response"],
            model_raw_output={"output": extracted_data.raw_response_text},
        )

    parsed_data = parse_extracted_data(extracted_data)
    parsed = extracted_data.parsed_json
    ingredients = parsed_data.ingredients
    label_nutrition = parsed_data.nutrition
    product_info = parsed_data.product_info

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
    )

    raw_text_value = parsed.get("raw_text") if isinstance(parsed.get("raw_text"), str) else None

    if extracted_data.visual_is_food is False:
        return OcrResult(
            ingredients=ingredients,
            nutrition_per_100g=None,
            product_info=product_info,
            visual_is_food=extracted_data.visual_is_food,
            visual_is_packaged_retail_food=extracted_data.visual_is_packaged_retail_food,
            visual_labels=extracted_data.visual_labels,
            parse_error=extracted_data.parse_error,
            raw_text=raw_text_value,
            extraction_metadata=extraction_metadata,
            warnings=[],
            errors=["Image was identified as non-food. Analysis stopped after visual assessment."],
            model_raw_output={"output": extracted_data.raw_response_text},
            nutrition_source="unavailable",
        )

    if extracted_data.visual_is_packaged_retail_food is False:
        return OcrResult(
            ingredients=ingredients,
            nutrition_per_100g=None,
            product_info=product_info,
            visual_is_food=extracted_data.visual_is_food,
            visual_is_packaged_retail_food=False,
            visual_labels=extracted_data.visual_labels,
            parse_error=extracted_data.parse_error,
            raw_text=raw_text_value,
            extraction_metadata=extraction_metadata,
            warnings=[],
            errors=[
                "This image does not appear to show a packaged retail product with a food label "
                "(for example loose produce or unpackaged food). Analysis stopped. "
                "This app is intended for scanning packaged foods with labels.",
            ],
            model_raw_output={"output": extracted_data.raw_response_text},
            nutrition_source="unavailable",
        )

    nutrition_resolution = resolve_nutrition_data(parsed_data, db)
    nutrition_data = nutrition_resolution.nutrition_data
    product_nutrition_match = nutrition_resolution.product_nutrition_match
    nutrition_from_product_db = (
        nutrition_resolution.nutrition_source
        == settings.reference_catalog_source_label
    )
    
    # Generate warnings and errors
    warnings: List[str] = []
    errors: List[str] = []
    
    if not extraction_metadata.ingredients_found:
        warnings.append("Ingredients list not found in image")
    
    if not extraction_metadata.nutrition_facts_found:
        warnings.append("Nutrition facts table not found in image")
    if nutrition_resolution.lookup_error:
        errors.append(nutrition_resolution.lookup_error)
    if nutrition_from_product_db:
        warnings.append(
            "Nutrition per 100 g/ml was filled from the reference nutrition lookup table "
            "because parsed label nutrition was unavailable."
        )

    if not is_usable_nutrition(nutrition_data):
        errors.append("Cannot perform KNPM labeling - nutrition data required")

    if not extraction_metadata.product_name_found:
        warnings.append("Product name not found in image")

    # If both ingredients and usable nutrition are missing, add error
    if not extraction_metadata.ingredients_found and not is_usable_nutrition(
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

    # Taxonomy from the same reference table as nutrition; BiLSTM only when lookup is null or weak.
    product_classification = lookup_product_classification_db(product_info, db)
    foodclasses_bilstm_prediction = None
    if settings.foodclasses_bilstm_enabled and product_info is not None:
        if not is_strong_catalog_classification(product_classification):
            foodclasses_bilstm_prediction = predict_foodclasses_from_product_text(
                product_info.name,
                product_info.brand,
            )
        product_classification = merge_foodclasses_with_classification(
            product_classification,
            foodclasses_bilstm_prediction,
        )

    knpm_label = classify_with_knpm(
        nutrition=nutrition_data,
        has_trans_fats=has_trans_fats if ingredients else False,
        has_sweeteners=has_sweeteners if ingredients else False,
    )

    # If we could not classify due to missing nutrition facts, surface that message as a warning.
    if knpm_label.message:
        warnings.append(knpm_label.message)

    model_raw: dict[str, Any] = {"output": extracted_data.raw_response_text}
    if product_nutrition_match is not None:
        model_raw["product_nutrition_match"] = product_nutrition_match.model_dump()
    if foodclasses_bilstm_prediction is not None:
        model_raw["foodclasses_bilstm_prediction"] = (
            foodclasses_bilstm_prediction.model_dump()
        )

    result = OcrResult(
        ingredients=ingredients,
        nutrition_per_100g=nutrition_data,
        product_info=product_info,
        visual_is_food=extracted_data.visual_is_food,
        visual_is_packaged_retail_food=extracted_data.visual_is_packaged_retail_food,
        visual_labels=extracted_data.visual_labels,
        parse_error=extracted_data.parse_error,
        raw_text=raw_text_value,
        extraction_metadata=extraction_metadata,
        warnings=warnings,
        errors=errors,
        model_raw_output=model_raw,
        knpm_label=knpm_label,
        nutrition_source=nutrition_resolution.nutrition_source,
        product_nutrition_match=product_nutrition_match,
        product_classification=product_classification,
        foodclasses_bilstm_prediction=foodclasses_bilstm_prediction,
    )
    return await attach_healthier_recommendations(
        result,
        has_trans_fats=has_trans_fats if ingredients else False,
        has_sweeteners=has_sweeteners if ingredients else False,
        user_goal=user_goal,
    )





