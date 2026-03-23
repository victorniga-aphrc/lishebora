import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv


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
    
    # Database settings
    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres@localhost:5432/lishebora"
    )

    # Supermarket POS taxonomy lookup (CSV: description, class_name, subclass_name, nova)
    supermarket_lookup_csv: Path = Path(
        os.getenv(
            "SUPERMARKET_LOOKUP_CSV",
            str(_REPO_ROOT / "data" / "product_class_subclass_lookup.csv"),
        )
    )
    supermarket_fuzzy_min_score: float = float(
        os.getenv("SUPERMARKET_FUZZY_MIN_SCORE", "72")
    )
    # When OCR category (e.g. "Fruit Drink") is matched to POS subclass/class labels
    supermarket_taxonomy_fuzzy_min_score: float = float(
        os.getenv("SUPERMARKET_TAXONOMY_FUZZY_MIN_SCORE", "52")
    )

    # Reference nutrition (CSV built from all_categories_combined) — fills gaps when label has no table
    reference_nutrition_lookup_csv: Path = Path(
        os.getenv(
            "REFERENCE_NUTRITION_LOOKUP_CSV",
            str(_REPO_ROOT / "data" / "reference_nutrition_lookup.csv"),
        )
    )
    reference_nutrition_fuzzy_min_score: float = float(
        os.getenv("REFERENCE_NUTRITION_FUZZY_MIN_SCORE", "72")
    )
    reference_nutrition_lookup_enabled: bool = os.getenv(
        "REFERENCE_NUTRITION_LOOKUP_ENABLED", "true"
    ).lower() in ("1", "true", "yes", "on")

    # KNPM official per-category nutrient limits (g per 100 g/ml) — not product nutrition values
    knpm_category_threshold_csv: Path = Path(
        os.getenv(
            "KNPM_CATEGORY_THRESHOLD_CSV",
            str(_REPO_ROOT / "data" / "knpm_category_threshold.csv"),
        )
    )
    knpm_category_fuzzy_min_score: float = float(
        os.getenv("KNPM_CATEGORY_FUZZY_MIN_SCORE", "55")
    )

    # NOVA BiLSTM (product name → 4-class NOVA). Requires tokenizer pickle from training.
    # Default off until tokenizer artifacts are confirmed.
    nova_bilstm_enabled: bool = os.getenv(
        "NOVA_BILSTM_ENABLED", "false"
    ).lower() in ("1", "true", "yes", "on")
    nova_bilstm_model_pkl: Path = Path(
        os.getenv(
            "NOVA_BILSTM_MODEL_PKL",
            str(_REPO_ROOT / "models" / "novaclasses_model.pkl"),
        )
    )
    nova_bilstm_tokenizer_pkl: Path = Path(
        os.getenv(
            "NOVA_BILSTM_TOKENIZER_PKL",
            str(_REPO_ROOT / "models" / "tokenizer.pkl"),
        )
    )
    nova_bilstm_labels_json: Path = Path(
        os.getenv(
            "NOVA_BILSTM_LABELS_JSON",
            str(_REPO_ROOT / "models" / "nova_labels.json"),
        )
    )
    nova_bilstm_label_encoders_pkl: Path = Path(
        os.getenv(
            "NOVA_BILSTM_LABEL_ENCODERS_PKL",
            str(_REPO_ROOT / "models" / "label_encoders.pkl"),
        )
    )
    # When POS match has no nova column, copy BiLSTM label onto supermarket_classification.nova
    nova_bilstm_fill_pos_nova: bool = os.getenv(
        "NOVA_BILSTM_FILL_POS_NOVA", "false"
    ).lower() in ("1", "true", "yes", "on")

    # Multi-head foodclasses BiLSTM (product name -> class/subclass/nova)
    foodclasses_bilstm_enabled: bool = os.getenv(
        "FOODCLASSES_BILSTM_ENABLED", "true"
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
    # If true, class/subclass/nova from foodclasses model override POS lookup.
    foodclasses_bilstm_prefer_over_pos: bool = os.getenv(
        "FOODCLASSES_BILSTM_PREFER_OVER_POS", "true"
    ).lower() in ("1", "true", "yes", "on")
    # Recommended mode: keep POS when strong, use model only when POS is weak/missing.
    foodclasses_bilstm_pos_first: bool = os.getenv(
        "FOODCLASSES_BILSTM_POS_FIRST", "true"
    ).lower() in ("1", "true", "yes", "on")
    # POS fuzzy score below this is considered weak (exact matches are always strong).
    foodclasses_bilstm_pos_weak_max_score: float = float(
        os.getenv("FOODCLASSES_BILSTM_POS_WEAK_MAX_SCORE", "70")
    )
    # Confidence guardrails for using foodclasses predictions.
    foodclasses_bilstm_min_class_confidence: float = float(
        os.getenv("FOODCLASSES_BILSTM_MIN_CLASS_CONFIDENCE", "0.60")
    )
    foodclasses_bilstm_min_subclass_confidence: float = float(
        os.getenv("FOODCLASSES_BILSTM_MIN_SUBCLASS_CONFIDENCE", "0.55")
    )
    foodclasses_bilstm_min_nova_confidence: float = float(
        os.getenv("FOODCLASSES_BILSTM_MIN_NOVA_CONFIDENCE", "0.40")
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

