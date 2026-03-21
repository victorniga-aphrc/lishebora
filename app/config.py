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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

