from typing import Any, List, Self

from pydantic import BaseModel, Field, model_validator


class Ingredient(BaseModel):
    """Single ingredient entry."""

    name: str = Field(..., description="Clean ingredient name")


class NutritionData(BaseModel):
    """
    Nutrition information per 100g or 100ml.
    """

    total_fat: float | None = Field(
        default=None, description="Total fat in grams per 100g/100ml"
    )
    trans_fat: float | None = Field(
        default=None, description="Trans fat in grams per 100g/100ml"
    )
    total_sugar: float | None = Field(
        default=None, description="Total sugar in grams per 100g/100ml"
    )
    sodium: float | None = Field(
        default=None, description="Sodium in grams per 100g/100ml"
    )


class ProductInfo(BaseModel):
    """Product identification information."""

    name: str | None = Field(default=None, description="Product name")
    brand: str | None = Field(default=None, description="Brand name")
    category: str | None = Field(
        default=None,
        description="Product category from label text when visible (e.g. snacks, beverages)",
    )
    visual_product_type: str | None = Field(
        default=None,
        description=(
            "Short plain-English product type inferred from the image (pack shape, logos, "
            "photos, layout) when text is missing or unclear — not internal stock codes. "
            "Used as a hint for taxonomy matching."
        ),
    )
    barcode: str | None = Field(default=None, description="Product barcode (if visible)")
    match_query_text: str | None = Field(
        default=None,
        description=(
            "Derived text used for downstream matching/model steps: name, brand, or "
            "'brand + name' depending on availability."
        ),
    )


class ExtractedData(BaseModel):
    """Raw grouped extraction output from the image-processing step."""

    raw_response_text: str | None = Field(
        default=None,
        description="Raw text returned by the vision model before parsing",
    )
    parsed_json: dict[str, Any] | None = Field(
        default=None,
        description="JSON-decoded model response when parsing succeeds",
    )
    raw_front_text: str | None = Field(
        default=None,
        description="Front-of-pack or main label text extracted from the image",
    )
    raw_ingredients_text: str | None = Field(
        default=None,
        description="Ingredients text extracted from the image",
    )
    raw_nutrition_table_text: str | None = Field(
        default=None,
        description="Nutrition table text extracted from the image",
    )
    detected_barcodes: List[str] = Field(
        default_factory=list,
        description="Barcodes detected from the image or model output",
    )
    detected_logos: List[str] = Field(
        default_factory=list,
        description="Brand or logo hints detected from the image",
    )
    visual_is_food: bool | None = Field(
        default=None,
        description="Whether the scene appears to contain a food product",
    )
    visual_is_packaged_retail_food: bool | None = Field(
        default=None,
        description=(
            "Whether the image shows a retail packaged product with label/pack graphics "
            "(false for loose produce, bulk unpackaged food, etc.)"
        ),
    )
    visual_labels: List[str] = Field(
        default_factory=list,
        description="Coarse visual labels inferred from the image",
    )
    visual_confidence: dict[str, float] = Field(
        default_factory=dict,
        description="Optional confidence values for visual predictions",
    )
    visual_notes: str | None = Field(
        default=None,
        description="Short free-text note about the visual interpretation",
    )
    parse_error: str | None = Field(
        default=None,
        description="Set when the model response could not be parsed into JSON",
    )


class ParsedData(BaseModel):
    """Structured output from the parsing step after raw extraction."""

    ingredients: List[Ingredient] = Field(
        default_factory=list,
        description="Parsed ingredient objects",
    )
    nutrition: NutritionData | None = Field(
        default=None,
        description="Parsed nutrition object with pipeline-active nutrients",
    )
    product_info: ProductInfo | None = Field(
        default=None,
        description="Parsed product information",
    )
    visual_is_food: bool | None = Field(
        default=None,
        description="Boolean food/non-food signal carried from the extraction step",
    )
    visual_is_packaged_retail_food: bool | None = Field(
        default=None,
        description="Packaged retail label scan suitability; carried from extraction",
    )
    visual_labels: List[str] = Field(
        default_factory=list,
        description="Visual labels carried from the extraction step",
    )


class ExtractionMetadata(BaseModel):
    """Metadata about what was successfully extracted from the image."""

    ingredients_found: bool = Field(
        default=False, description="Whether ingredients list was found"
    )
    nutrition_facts_found: bool = Field(
        default=False, description="Whether nutrition facts table was found"
    )
    product_name_found: bool = Field(
        default=False, description="Whether product name was found"
    )
    barcode_found: bool = Field(
        default=False, description="Whether barcode was found"
    )


class ReferenceNutritionMatch(BaseModel):
    """Metadata for per-100g values resolved from the reference nutrition table."""

    matched_product_name: str = Field(
        ...,
        description="Reference row product_name that was matched",
    )
    match_method: str | None = Field(
        default=None,
        description="e.g. exact_name, fuzzy_combined",
    )
    match_score: float | None = Field(
        default=None,
        description="Fuzzy score (0–100) when match_method is fuzzy_*",
    )
    sub_type: str | None = Field(
        default=None,
        description="Source sub_type from reference row (if present)",
    )
    form: str | None = Field(
        default=None,
        description="Source form from reference row (Solid/Liquid/Paste, if present)",
    )


class ProductNutritionMatchMetadata(BaseModel):
    """Metadata for per-100g values resolved from the reference nutrition lookup table."""

    row_id: int | None = Field(
        default=None,
        description="Matched row ID when the source table exposes one",
    )
    match_method: str | None = Field(
        default=None,
        description="How the lookup match was made, e.g. db_exact_name or db_fuzzy_name",
    )
    matched_product_name: str | None = Field(
        default=None,
        description="Matched product name from the reference nutrition lookup table",
    )
    match_score: float | None = Field(
        default=None,
        description="Fuzzy score (0–100) when match_method is db_fuzzy_name",
    )
    sub_type: str | None = Field(
        default=None,
        description="From reference row when present",
    )
    form: str | None = Field(
        default=None,
        description="Solid/Liquid/Paste from reference row when present",
    )


class NutritionResolution(BaseModel):
    """Resolved Step 3 nutrition output used by later pipeline steps."""

    nutrition_data: NutritionData | None = Field(
        default=None,
        description="Resolved nutrition after label and product-db fallback",
    )
    nutrition_source: str = Field(
        default="unavailable",
        description=(
            "Source of resolved nutrition. One of: "
            "'image' (parsed from label), "
            "'catalog.product_nutrition' (PRIMARY product table hit), "
            "'catalog.food_composition_reference' (SECONDARY reference fallback when "
            "the primary table had no match - sugar may be missing), "
            "or 'unavailable'."
        ),
    )
    product_nutrition_match: ProductNutritionMatchMetadata | None = Field(
        default=None,
        description="Metadata for a successful reference nutrition lookup (same table as taxonomy)",
    )
    lookup_error: str | None = Field(
        default=None,
        description="Set when the database nutrition lookup could not be performed",
    )


class FoodclassesBiLstmPrediction(BaseModel):
    """
    Class/subclass/NOVA from multi-head BiLSTM (``foodclasses_model.pkl``).

    Confidence fields are always returned when the model runs; they are not used to
    decide whether to adopt the labels (that is DB strong-match vs model-only).

    ``nova_label`` is the canonical line from ``models/nova_labels.json`` (see ``normalize_nova_for_api``).
    """

    class_name: str | None = Field(default=None, description="Predicted class_name label")
    subclass_name: str | None = Field(
        default=None, description="Predicted subclass_name label"
    )
    nova_label: str | None = Field(
        default=None,
        description="Canonical NOVA display string from nova_labels.json",
    )
    class_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    subclass_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    nova_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    input_text: str = Field(
        ..., description="Product string passed to the model (e.g. brand + name)"
    )


class ClassifierPrediction(BaseModel):
    """
    Runtime classifier output. Produced by the OpenAI classifier
    (``app.services.openai_classifier``) when a strong catalog match is unavailable.

    Confidence is the model's self-reported 1-5 score (5 = definitely correct).
    ``needs_review`` is set when confidence is below the configured review threshold
    so the UI can highlight uncertain predictions.
    """

    class_name: str | None = Field(default=None, description="Predicted class_name label")
    subclass_name: str | None = Field(
        default=None, description="Predicted subclass_name label"
    )
    nova: str | None = Field(default=None, description="Predicted NOVA category")
    confidence: int | None = Field(
        default=None,
        ge=1,
        le=5,
        description="Self-reported confidence 1-5 (5 = definitely correct)",
    )
    needs_review: bool = Field(
        default=False,
        description="True when confidence is below the review threshold",
    )
    reason: str | None = Field(
        default=None,
        description="Short rationale from the model (e.g. the food noun it used)",
    )
    source: str = Field(
        default="openai",
        description="Which classifier produced this row: 'openai' | 'cache' | 'manual'",
    )
    model_used: str | None = Field(
        default=None,
        description="Model identifier (e.g. 'gpt-4o')",
    )
    cached: bool = Field(
        default=False,
        description="True when the prediction was served from catalog.classification_cache",
    )
    input_text: str = Field(
        ..., description="Product string passed to the classifier (brand + name)"
    )


class ProductClassification(BaseModel):
    """
    Product taxonomy classification for the scanned product.

    Resolution: match OCR product name (and brand) to the reference catalog row; class_name,
    subclass_name, and NOVA come from that row when the name match is strong enough.
    """

    class_name: str | None = Field(default=None, description="Taxonomy class name")
    subclass_name: str | None = Field(default=None, description="Taxonomy subclass name")
    nova: str | None = Field(default=None, description="NOVA processing category from reference catalog")
    matched_description: str | None = Field(
        default=None,
        description="Reference catalog product_name (or best fuzzy match) used for taxonomy",
    )
    match_method: str | None = Field(
        default=None,
        description=(
            "How the match was made: exact_name, fuzzy_combined, "
            "taxonomy_subclass_from_category, taxonomy_subclass_from_visual_product_type, "
            "taxonomy_subclass_from_combined, taxonomy_class_from_* (same suffixes), etc."
        ),
    )
    match_score: float | None = Field(
        default=None,
        description="Fuzzy match score (0–100); null for exact name matches",
    )


class SubstituteProduct(BaseModel):
    """One healthier alternative from the reference nutrition catalog."""

    product_name: str = Field(..., description="Reference catalog product name")
    tier: int = Field(..., ge=1, le=3, description="1 same subclass, 2 same class, 3 broader pool")
    class_name: str | None = Field(default=None, description="Taxonomy class when known")
    subclass_name: str | None = Field(default=None, description="Taxonomy subclass when known")
    octagon_count: int = Field(..., ge=0, description="KNPM black-octagon count under scan category limits")
    octagons: List[str] = Field(
        default_factory=list,
        description="high_in_sugar / high_in_salt / high_in_fat from numeric KNPM only",
    )
    below_knpm_thresholds: bool = Field(
        ...,
        description="True when no numeric octagons for this product under the scan's KNPM category limits",
    )
    sub_type: str | None = Field(default=None, description="From reference row when present")
    form: str | None = Field(default=None, description="Solid/Liquid/Paste from reference when present")


class HealthierSubstituteResult(BaseModel):
    """
    Tiered substitute list (content-based / catalog).

    True collaborative filtering (co-purchase or matrix factorization) can be added later
    using scan logs; this version ranks reference products by taxonomy proximity and KNPM health.
    """

    triggered: bool = Field(
        default=False,
        description="False when scan is not flagged as less healthy / no substitutes run",
    )
    skip_reason: str | None = Field(
        default=None,
        description="Why recommendations were skipped (e.g. fit for consumption, disabled)",
    )
    exceeded_nutrient_summary: List[str] = Field(
        default_factory=list,
        description="Short tags for nutrients / conditions that triggered concern (for UI + GenAI)",
    )
    tier_used: int = Field(
        default=1,
        ge=1,
        le=3,
        description="Widest tier used to fill the result list (1 narrowest)",
    )
    no_close_substitutes: bool = Field(
        default=False,
        description="True when Tier 1 had no below-threshold options and we expanded to wider tiers",
    )
    inferred_scan_form: str | None = Field(
        default=None,
        description="liquid | solid | paste inferred from label and reference/product text for substitute ranking",
    )
    inferred_substitute_use_context: str | None = Field(
        default=None,
        description="When set (e.g. beverage_drink), pantry oils/vinegars are deprioritised vs juices/soft drinks",
    )
    substitutes_include_other_forms: bool = Field(
        default=False,
        description="True when scan form was inferred and at least one substitute has a different known form",
    )
    substitutes_include_pantry_liquids: bool = Field(
        default=False,
        description="True when a drink-like scan still lists oil/vinegar-type liquids among substitutes",
    )
    substitutes: List[SubstituteProduct] = Field(default_factory=list)
    explanation: str | None = Field(
        default=None,
        description="Short GenAI (or template) narrative comparing scan to alternatives",
    )
    approach_note: str = Field(
        default=(
            "Ranked from PostgreSQL reference nutrition data using taxonomy matching "
            "and the same KNPM category limits as this scan. Collaborative filtering from "
            "user/scan co-occurrence can be layered on top later."
        ),
        description="How substitutes were chosen (content-based catalog)",
    )


class KnpmLabel(BaseModel):
    """
    KNPM-based classification for a product.
    """

    classification: str | None = Field(
        default=None,
        description="Overall classification: healthy, less healthy, or unknown",
    )
    octagons: List[str] = Field(
        default_factory=list,
        description=(
            "Specific black-octagon warnings, e.g. high_in_sugar, high_in_salt, high_in_fat. "
            "Empty if product is fit for consumption or cannot be evaluated."
        ),
    )
    reasons: List[str] = Field(
        default_factory=list,
        description="Human-readable reasons explaining the classification and octagons.",
    )
    message: str | None = Field(
        default=None,
        description=(
            "Optional message when classification cannot be applied (e.g. missing nutrition facts)."
        ),
    )


class OcrResult(BaseModel):
    """Structured output from the OCR / extraction step."""

    ingredients: List[Ingredient] = Field(
        default_factory=list,
        description="List of cleaned ingredients parsed from the label",
    )
    nutrition_per_100g: NutritionData | None = Field(
        default=None,
        description="Nutrition information per 100g/100ml (if available)",
    )
    product_info: ProductInfo | None = Field(
        default=None, description="Product identification information (if available)"
    )
    visual_is_food: bool | None = Field(
        default=None,
        description=(
            "Boolean visual assessment of whether the image shows an edible item. "
            "True includes foods, drinks, spices, condiments, and cooking ingredients; "
            "false means non-food; null means uncertain."
        ),
    )
    visual_labels: List[str] = Field(
        default_factory=list,
        description="Plain-English visual labels inferred from the image",
    )
    visual_is_packaged_retail_food: bool | None = Field(
        default=None,
        description=(
            "True if vision assessed a retail packaged product with a label; false if unpackaged "
            "(e.g. loose produce) or not a consumer pack scan"
        ),
    )
    parse_error: str | None = Field(
        default=None,
        description="Set when the raw extraction response could not be parsed into JSON",
    )
    class_name: str | None = Field(
        default=None,
        description="Resolved taxonomy class for this scan (mirrors product_classification; null if unresolved)",
    )
    subclass_name: str | None = Field(
        default=None,
        description="Resolved taxonomy subclass for this scan (mirrors product_classification; null if unresolved)",
    )
    raw_text: str | None = Field(
        default=None,
        description="Raw text extracted from the image (for debugging)",
    )
    extraction_metadata: ExtractionMetadata = Field(
        default_factory=lambda: ExtractionMetadata(
            ingredients_found=False,
            nutrition_facts_found=False,
            product_name_found=False,
            barcode_found=False,
        ),
        description="Metadata about what was successfully extracted",
    )
    warnings: List[str] = Field(
        default_factory=list,
        description="Warnings about missing or incomplete data",
    )
    errors: List[str] = Field(
        default_factory=list,
        description="Errors that prevent further processing",
    )
    model_raw_output: Any | None = Field(
        default=None,
        description="Raw output returned by the vision model call (for debugging)",
    )
    knpm_label: KnpmLabel | None = Field(
        default=None,
        description="KNPM-based label and black octagon warnings (if nutrition data is available)",
    )
    product_classification: ProductClassification | None = Field(
        default=None,
        description="Full taxonomy lookup result (NOVA, match method, scores). class_name/subclass_name above are copied from here.",
    )
    nutrition_source: str = Field(
        default="unavailable",
        description=(
            "Where nutrition_per_100g came from. One of: "
            "'image' (parsed from label), "
            "'catalog.product_nutrition' (PRIMARY retail SKU match), "
            "'catalog.food_composition_reference' (SECONDARY fallback for generic foods; "
            "sugar may be missing from this source), "
            "or 'unavailable'."
        ),
    )
    product_nutrition_match: ProductNutritionMatchMetadata | None = Field(
        default=None,
        description=(
            "Set when nutrition_per_100g was filled from a DB lookup. The match_method "
            "field distinguishes primary (db_*) from secondary (reference_*) sources."
        ),
    )
    classifier_prediction: ClassifierPrediction | None = Field(
        default=None,
        description=(
            "Active runtime classifier prediction (OpenAI). Includes self-reported "
            "confidence (1-5), needs_review flag, and which model produced the result."
        ),
    )
    healthier_substitutes: HealthierSubstituteResult | None = Field(
        default=None,
        description="Tiered healthier alternatives when the product is less healthy (KNPM)",
    )

    @model_validator(mode="after")
    def _sync_taxonomy_class_subclass_from_lookup(self) -> Self:
        """Expose class/subclass at top level for API JSON (same as product_classification)."""
        sc = self.product_classification
        if sc is not None:
            self.class_name = sc.class_name
            self.subclass_name = sc.subclass_name
        else:
            self.class_name = None
            self.subclass_name = None
        return self

