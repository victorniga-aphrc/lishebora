import os
from functools import lru_cache

from dotenv import load_dotenv


load_dotenv()


class Settings:
    """Application settings loaded from environment variables."""

    app_name: str = "Lishebora VIC Backend"
    replicate_api_token: str | None = os.getenv("REPLICATE_API_TOKEN")
    # Optional: model identifier / version for Replicate vision or OCR model.
    # Defaults to OpenAI GPT-4.1 mini hosted on Replicate.
    replicate_model: str = os.getenv("REPLICATE_MODEL", "openai/gpt-4.1-mini")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

