import os
from functools import lru_cache

from dotenv import load_dotenv


load_dotenv()


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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

