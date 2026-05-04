import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

from app.database.identifiers import dotted_name, qualified_table


load_dotenv()

_REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings:
    """Application settings loaded from environment variables."""

    app_name: str = "Lishebora VIC Backend"
    # OpenAI settings (preferred path going forward)
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    
    # Legacy Replicate settings (kept for backwards compatibility; not used by default)
    replicate_api_token: str | None = os.getenv("REPLICATE_API_TOKEN")
    replicate_model: str = os.getenv("REPLICATE_MODEL", "openai/gpt-4.1-mini")
    
    # Database settings (database name is the path segment after the host, e.g. .../lishebora)
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres@localhost:5432/lishebora",
    )

    # Catalog schema (shared by primary product table and secondary reference table).
    reference_catalog_schema: str = (
        os.getenv("REFERENCE_CATALOG_SCHEMA", "catalog").strip().lower()
    )

    # PRIMARY: catalog.product_nutrition (3,973 retail SKUs with octagon_count).
    # Write-through enabled (each successful scan upserts here).
    reference_catalog_products_table: str = (
        os.getenv("REFERENCE_CATALOG_PRODUCTS_TABLE", "product_nutrition")
        .strip()
        .lower()
    )

    # SECONDARY: catalog.food_composition_reference (654 standard food composition entries).
    # Read-only TRUE fallback consulted only when the PRIMARY table cannot match the scanned
    # product at all (no exact, no fuzzy hit). Has fat/sodium but no sugar.
    food_composition_reference_table: str = (
        os.getenv("FOOD_COMPOSITION_REFERENCE_TABLE", "food_composition_reference")
        .strip()
        .lower()
    )

    @property
    def reference_catalog_qualified_sql(self) -> str:
        """Quoted schema.table for the PRIMARY product nutrition table."""
        return qualified_table(
            schema=self.reference_catalog_schema,
            table=self.reference_catalog_products_table,
        )

    @property
    def reference_catalog_source_label(self) -> str:
        """Human/API label for nutrition_source (unquoted schema.table)."""
        return dotted_name(
            schema=self.reference_catalog_schema,
            table=self.reference_catalog_products_table,
        )

    @property
    def food_composition_reference_qualified_sql(self) -> str:
        """Quoted schema.table for the SECONDARY food composition reference table."""
        return qualified_table(
            schema=self.reference_catalog_schema,
            table=self.food_composition_reference_table,
        )

    @property
    def food_composition_reference_source_label(self) -> str:
        """Human/API label for the secondary nutrient source."""
        return dotted_name(
            schema=self.reference_catalog_schema,
            table=self.food_composition_reference_table,
        )

    # Minimum SequenceMatcher score (0–100) for fuzzy name match vs catalog; exact normalized
    # name match is always used first and ignores this threshold.
    reference_catalog_fuzzy_min_score: float = float(
        os.getenv("REFERENCE_CATALOG_FUZZY_MIN_SCORE", "90")
    )

    # Active KNPM thresholds used in runtime classification (g per 100g/ml)
    knpm_fat_threshold: float = float(os.getenv("KNPM_FAT_THRESHOLD", "7.76"))
    knpm_sugar_threshold: float = float(os.getenv("KNPM_SUGAR_THRESHOLD", "4.7"))
    knpm_sodium_threshold: float = float(os.getenv("KNPM_SODIUM_THRESHOLD", "0.26"))

    # === Runtime classifier (OpenAI only) ===
    # OpenAI is the sole product classifier when the catalog match is weak.
    # BiLSTM is intentionally NOT wired into the runtime; the model files and
    # service code remain in the repo for possible future re-activation.
    openai_classifier_enabled: bool = os.getenv(
        "OPENAI_CLASSIFIER_ENABLED", "true"
    ).lower() in ("1", "true", "yes", "on")
    openai_classifier_model: str = os.getenv(
        "OPENAI_CLASSIFIER_MODEL", "gpt-4o"
    ).strip()
    # Confidence (1-5) below which a prediction is flagged needs_review.
    openai_classifier_review_threshold: int = int(
        os.getenv("OPENAI_CLASSIFIER_REVIEW_THRESHOLD", "4")
    )
    # OpenAI request timeout (seconds). Keep this tight on the hot path.
    openai_classifier_timeout_s: float = float(
        os.getenv("OPENAI_CLASSIFIER_TIMEOUT_S", "8.0")
    )
    # Cache table for OpenAI classifier results (catalog schema reused).
    classification_cache_table: str = (
        os.getenv("CLASSIFICATION_CACHE_TABLE", "classification_cache")
        .strip()
        .lower()
    )

    @property
    def classification_cache_qualified_sql(self) -> str:
        return qualified_table(
            schema=self.reference_catalog_schema,
            table=self.classification_cache_table,
        )

    # BiLSTM legacy settings: kept only because foodclasses_bilstm_inference.py
    # still reads them. Default to disabled so the BiLSTM model is never loaded.
    foodclasses_bilstm_enabled: bool = os.getenv(
        "FOODCLASSES_BILSTM_ENABLED", "false"
    ).lower() in ("1", "true", "yes", "on")
    foodclasses_bilstm_model_pkl: Path = Path(
        os.getenv(
            "FOODCLASSES_BILSTM_MODEL_PKL",
            str(_REPO_ROOT / "models" / "foodclasses_model.pkl"),
        )
    )
    foodclasses_bilstm_tokenizer_pkl: Path = Path(
        os.getenv(
            "FOODCLASSES_BILSTM_TOKENIZER_PKL",
            str(_REPO_ROOT / "models" / "tokenizer.pkl"),
        )
    )
    foodclasses_bilstm_label_encoders_pkl: Path = Path(
        os.getenv(
            "FOODCLASSES_BILSTM_LABEL_ENCODERS_PKL",
            str(_REPO_ROOT / "models" / "label_encoders.pkl"),
        )
    )
    # Fuzzy name score below this (vs reference_catalog row) is weak; exact name match is always strong.
    foodclasses_bilstm_reference_weak_max_score: float = float(
        os.getenv("FOODCLASSES_BILSTM_REFERENCE_WEAK_MAX_SCORE", "70")
    )
    # Official NOVA display strings (index → line); used for API normalization.
    nova_labels_json: Path = Path(
        os.getenv(
            "NOVA_LABELS_JSON",
            str(_REPO_ROOT / "models" / "nova_labels.json"),
        )
    )

    # Healthier substitutes (reference catalog + KNPM tiers; optional GenAI blurb)
    substitute_recommendations_enabled: bool = os.getenv(
        "SUBSTITUTE_RECOMMENDATIONS_ENABLED", "true"
    ).lower() in ("1", "true", "yes", "on")
    substitute_max_results: int = int(os.getenv("SUBSTITUTE_MAX_RESULTS", "3"))
    substitute_min_results: int = int(os.getenv("SUBSTITUTE_MIN_RESULTS", "3"))
    substitute_explanation_enabled: bool = os.getenv(
        "SUBSTITUTE_EXPLANATION_ENABLED", "true"
    ).lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

