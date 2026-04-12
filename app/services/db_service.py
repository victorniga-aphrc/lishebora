"""Persist each successful extract as one row in ``app.product_scan_summary``."""

from datetime import datetime
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database.models import ProductScanSummary
from app.models import OcrResult
from app.services.reference_catalog_write import upsert_reference_product_from_ocr
from app.utils.nova_display import normalize_nova_for_api


def _octagon_count(ocr: OcrResult) -> int:
    k = ocr.knpm_label
    if not k or not k.octagons:
        return 0
    return len(k.octagons)


def _resolve_nova(ocr: OcrResult) -> str | None:
    pc = ocr.product_classification
    if pc and pc.nova and str(pc.nova).strip():
        return normalize_nova_for_api(str(pc.nova).strip())
    b = ocr.foodclasses_bilstm_prediction
    if b and b.nova_label and str(b.nova_label).strip():
        return normalize_nova_for_api(str(b.nova_label).strip())
    return None


def save_ocr_result_to_db(
    db: Session,
    ocr_result: OcrResult,
    user_id: Optional[str] = None,
    location: Optional[str] = None,
    image_path: Optional[str] = None,
) -> int:
    """
    Insert one summary row for this extract and merge into ``catalog.reference_products``
    (exact normalized name match; existing rows only get NULL columns filled). Catalog
    writes run in a **savepoint** so a logged catalog failure does not block the summary
    ``commit``.

    Returns:
        New ``product_scan_summary.id``.
    """
    pi = ocr_result.product_info
    nut = ocr_result.nutrition_per_100g
    now = datetime.utcnow()
    row = ProductScanSummary(
        product_name=pi.name if pi else None,
        brand=pi.brand if pi else None,
        barcode=pi.barcode if pi else None,
        total_fat_g=nut.total_fat if nut else None,
        sodium_g=nut.sodium if nut else None,
        total_sugar_g=nut.total_sugar if nut else None,
        class_name=ocr_result.class_name,
        subclass_name=ocr_result.subclass_name,
        nova=_resolve_nova(ocr_result),
        octagon_count=_octagon_count(ocr_result),
        user_id=user_id,
        location=location,
        image_path=image_path,
        created_at=now,
    )
    db.add(row)
    try:
        with db.begin_nested():
            upsert_reference_product_from_ocr(db, ocr_result)
    except SQLAlchemyError:
        # ERROR log + traceback emitted in ``upsert_reference_product_from_ocr``.
        pass
    db.commit()
    db.refresh(row)
    return row.id
