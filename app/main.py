from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from pydantic import BaseModel

from app.db import get_db
from app.models import HealthierSubstituteResult, OcrResult
from app.services.db_service import save_ocr_result_to_db
from app.services.ocr_client import OcrClientError, process_food_product
from app.services.recommendation_explainer import attach_healthier_recommendations


_REPO_ROOT = Path(__file__).resolve().parent.parent
_INDEX_HTML = _REPO_ROOT / "static" / "index.html"

app = FastAPI(title="Lishebora VIC Backend")


def _has_usable_nutrition(result: OcrResult) -> bool:
    n = result.nutrition_per_100g
    if n is None:
        return False
    return any(v is not None for v in (n.total_fat, n.total_sugar, n.sodium))


# Serve black octagon SVGs under /octagon_images
app.mount(
    "/octagon_images",
    StaticFiles(directory="octagon_images"),
    name="octagon_images",
)


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """Pipeline test console (static/index.html)."""
    if not _INDEX_HTML.is_file():
        return "<!doctype html><html><body><p>Missing static/index.html</p></body></html>"
    return _INDEX_HTML.read_text(encoding="utf-8")


class SubstituteRecommendRequest(BaseModel):
    """Prior scan JSON plus optional shopper goal for the substitute explainer."""

    ocr_result: OcrResult
    user_goal: str | None = None


@app.post("/extract", response_model=OcrResult)
async def extract(
    image: UploadFile = File(...),
    user_goal: str | None = Form(None),
    db: Session = Depends(get_db),
) -> OcrResult:
    """
    Accept an image upload, extract data, save to database, and return structured OCR result.

    Optional form field ``user_goal`` is only passed through to the substitute explainer when
    GenAI explanations are enabled; ranking does not use it.

    On success, one flat row is appended to PostgreSQL ``app.product_scan_summary``
    (name, brand, barcode, nutrients, taxonomy, NOVA, octagon count), and the same
    extract is merged into ``catalog.reference_products`` when the DB is configured.
    """
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are accepted.")

    try:
        image_bytes = await image.read()
        result = await process_food_product(
            image_bytes,
            user_goal=user_goal,
            db=db,
        )

        # Save to database only when there is readable text and a name/brand anchor.
        pi = result.product_info
        has_readable_text = bool((result.raw_text or "").strip())
        has_name_or_brand = bool(
            pi and (bool((pi.name or "").strip()) or bool((pi.brand or "").strip()))
        )
        no_usable_nutrition_and_no_db_match = (
            (not _has_usable_nutrition(result))
            and (result.product_nutrition_match is None)
        )
        if has_readable_text and has_name_or_brand and not no_usable_nutrition_and_no_db_match:
            try:
                save_ocr_result_to_db(
                    db=db,
                    ocr_result=result,
                    user_id=None,  # TODO: Add authentication
                    location=None,  # TODO: Extract from request if available
                    image_path=None,  # TODO: Save image to storage if needed
                )
            except Exception as db_exc:
                # Log database error but don't fail the request
                # The OCR extraction was successful, so we still return the result
                print(f"Warning: Failed to save to database: {db_exc}")
        
        return result
    except OcrClientError as exc:
        code = getattr(exc, "status_code", 502)
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail="Unexpected error during OCR.") from exc


@app.post("/recommend/substitutes", response_model=HealthierSubstituteResult)
async def recommend_substitutes(payload: SubstituteRecommendRequest) -> HealthierSubstituteResult:
    """
    Recompute healthier substitutes + explanation from a prior ``OcrResult`` JSON
    (e.g. after editing ``user_goal`` without re-running vision OCR).
    """
    updated = await attach_healthier_recommendations(
        payload.ocr_result,
        has_trans_fats=False,
        has_sweeteners=False,
        user_goal=payload.user_goal,
    )
    hs = updated.healthier_substitutes
    if hs is None:
        return HealthierSubstituteResult(
            triggered=False,
            skip_reason="Substitute recommendations disabled.",
        )
    return hs


@app.get("/health", response_class=JSONResponse)
async def health() -> dict:
    """Simple health check endpoint."""
    return {"status": "ok"}

