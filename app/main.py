from pathlib import Path
import logging

from fastapi import Depends, FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from pydantic import BaseModel

from app.db import get_db
from app.models import HealthierSubstituteResult, OcrResult
from app.services.db_service import save_ocr_result_to_db
from app.services.image_storage import get_image_storage
from app.services.ocr_client import OcrClientError, process_food_product
from app.services.recommendation_explainer import attach_healthier_recommendations

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)


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
    extract is merged into ``catalog.product_nutrition`` when the DB is configured.
    """
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are accepted.")

    try:
        logger.info(
            "[extract] request started | filename=%s content_type=%s",
            image.filename or "(unknown)",
            image.content_type or "(unknown)",
        )
        image_bytes = await image.read()
        logger.info("[extract] image bytes read | size=%d", len(image_bytes))

        # Persist every uploaded image (including non-food / failed scans) up front,
        # so the originals are available later for OCR quality review. Storage failures
        # must never break the scan itself.
        stored_key: str | None = None
        try:
            stored_key = get_image_storage().save(image_bytes, image.content_type)
            logger.info("[extract] image stored | key=%s", stored_key)
        except Exception as store_exc:  # pragma: no cover - defensive
            logger.warning("[extract] failed to store scan image: %s", store_exc)

        result = await process_food_product(
            image_bytes,
            user_goal=user_goal,
            db=db,
        )
        logger.info("[extract] OCR pipeline completed")

        if stored_key:
            result.image_path = stored_key
            result.image_url = get_image_storage().url_for(stored_key)

        # Save to database only when there is readable text and a product-name anchor.
        pi = result.product_info
        has_readable_text = bool((result.raw_text or "").strip())
        has_product_name = bool(pi and bool((pi.name or "").strip()))
        no_usable_nutrition_and_no_db_match = (
            (not _has_usable_nutrition(result))
            and (result.product_nutrition_match is None)
        )
        if has_readable_text and has_product_name and not no_usable_nutrition_and_no_db_match:
            try:
                logger.info("[extract] saving scan summary to DB")
                save_ocr_result_to_db(
                    db=db,
                    ocr_result=result,
                    user_id=None,  # TODO: Add authentication
                    location=None,  # TODO: Extract from request if available
                    image_path=stored_key,
                )
            except Exception as db_exc:
                # Log database error but don't fail the request
                # The OCR extraction was successful, so we still return the result
                logger.warning("Failed to save scan summary to DB: %s", db_exc)
        
        logger.info("[extract] request finished successfully")
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


@app.get("/scans/image/{key:path}")
async def get_scan_image(key: str) -> FileResponse:
    """Serve a stored scan image by its storage key (see ``OcrResult.image_url``)."""
    path = get_image_storage().resolve(key)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Image not found.")
    return FileResponse(path)


@app.get("/health", response_class=JSONResponse)
async def health() -> dict:
    """Simple health check endpoint."""
    return {"status": "ok"}

