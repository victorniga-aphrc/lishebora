from typing import Any, List

from pydantic import BaseModel, Field


class Ingredient(BaseModel):
    """Single ingredient entry."""

    name: str = Field(..., description="Clean ingredient name")


class OcrResult(BaseModel):
    """Structured output from the OCR / extraction step."""

    ingredients: List[Ingredient] = Field(
        default_factory=list,
        description="List of cleaned ingredients parsed from the label",
    )
    raw_text: str | None = Field(
        default=None,
        description="Raw text extracted from the image (for debugging)",
    )
    model_raw_output: Any | None = Field(
        default=None,
        description="Raw output returned by the Replicate model (for debugging)",
    )

