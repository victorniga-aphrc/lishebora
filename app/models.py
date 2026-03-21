from typing import Any, List, Self

from pydantic import BaseModel, Field, model_validator


class Ingredient(BaseModel):
    """Single ingredient entry."""

    name: str = Field(..., description="Clean ingredient name")


class NutritionData(BaseModel):
    """
    Nutrition information per 100g or 100ml.
    
    Core KNPM nutrients are explicit fields for easy access.
    Additional nutrients (potassium, calcium, iron, etc.) are stored in
    additional_nutrients dict to capture everything visible on the label.
    """

    # Core KNPM nutrients (explicit fields for easy access)
    energy_kcal: float | None = Field(
        default=None, description="Energy in kilocalories per 100g/100ml"
    )
    total_fat: float | None = Field(
        default=None, description="Total fat in grams per 100g/100ml"
    )
    saturated_fat: float | None = Field(
        default=None, description="Saturated fat in grams per 100g/100ml"
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
    protein: float | None = Field(
        default=None, description="Protein in grams per 100g/100ml"
    )
    carbohydrates: float | None = Field(
        default=None, description="Carbohydrates in grams per 100g/100ml"
    )
    fiber: float | None = Field(
        default=None, description="Dietary fiber in grams per 100g/100ml"
    )
    
    # Additional nutrients (potassium, calcium, iron, vitamins, etc.)
    # Stored as dict to capture any nutrients present on the label
    additional_nutrients: dict[str, float] = Field(
        default_factory=dict,
        description="Additional nutrients found on the label (e.g., potassium, calcium, iron, vitamins) with their values per 100g/100ml"
    )


class ProductInfo(BaseModel):
    """Product identification information."""

    name: str | None = Field(default=None, description="Product name")
    brand: str | None = Field(default=None, description="Brand name")
    category: str | None = Field(
        default=None, description="Product category (e.g., snacks, beverages)"
    )
    barcode: str | None = Field(default=None, description="Product barcode (if visible)")


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


class SupermarketClassification(BaseModel):
    """
    Supermarket POS taxonomy for the scanned product.

    Resolution: (1) OCR name/brand vs POS SKU ``description``; (2) if needed, OCR
    ``category`` vs distinct POS ``subclass_name`` / ``class_name`` (for products
    not listed verbatim and for healthy-alternative bucketing).
    """

    class_name: str | None = Field(default=None, description="POS class name")
    subclass_name: str | None = Field(default=None, description="POS subclass name")
    nova: str | None = Field(default=None, description="NOVA processing category from POS")
    matched_description: str | None = Field(
        default=None,
        description="Matched POS SKU line, or representative SKU when matched via category→taxonomy",
    )
    match_method: str | None = Field(
        default=None,
        description="How the match was made, e.g. exact_name, fuzzy_combined",
    )
    match_score: float | None = Field(
        default=None,
        description="Fuzzy match score (0–100); null for exact SKU matches",
    )


class KnpmLabel(BaseModel):
    """
    KNPM-based classification for a product.
    """

    classification: str | None = Field(
        default=None,
        description="Overall classification: FIT_FOR_CONSUMPTION, LESS_HEALTHY, or UNKNOWN",
    )
    octagons: List[str] = Field(
        default_factory=list,
        description=(
            "Specific black-octagon warnings, e.g. HIGH_IN_SUGAR, HIGH_IN_SALT, HIGH_IN_FAT. "
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
    class_name: str | None = Field(
        default=None,
        description="Supermarket POS class for this scan (mirrors lookup; null if unresolved)",
    )
    subclass_name: str | None = Field(
        default=None,
        description="Supermarket POS subclass for this scan (mirrors lookup; null if unresolved)",
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
        description="Raw output returned by the Replicate model (for debugging)",
    )
    knpm_label: KnpmLabel | None = Field(
        default=None,
        description="KNPM-based label and black octagon warnings (if nutrition data is available)",
    )
    supermarket_classification: SupermarketClassification | None = Field(
        default=None,
        description="Full POS lookup result (NOVA, match method, scores). class_name/subclass_name above are copied from here.",
    )

    @model_validator(mode="after")
    def _sync_pos_class_subclass_from_lookup(self) -> Self:
        """Expose POS class/subclass at top level for API JSON (same as supermarket_classification)."""
        sc = self.supermarket_classification
        if sc is not None:
            return self.model_copy(
                update={
                    "class_name": sc.class_name,
                    "subclass_name": sc.subclass_name,
                }
            )
        return self.model_copy(update={"class_name": None, "subclass_name": None})

