"""SQLAlchemy database models (minimal persistence: ``app.product_scan_summary`` only)."""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Identity, Integer, String, Text
from app.db import Base

APP_SCHEMA = "app"


class ProductScanSummary(Base):
    """
    One relational row per successful ``/extract`` save (flat fields for reporting).

    Reference catalog lives in ``catalog.product_nutrition`` and
    ``catalog.food_composition_reference`` (modeled in app.database.nutrition_models).
    """

    __tablename__ = "product_scan_summary"
    __table_args__ = {"schema": APP_SCHEMA}

    id = Column(Integer, Identity(always=False), primary_key=True, index=True)
    product_name = Column(String(255), nullable=True)
    brand = Column(String(255), nullable=True)
    barcode = Column(String(50), nullable=True)
    total_fat_g = Column(Float, nullable=True)
    sodium_g = Column(Float, nullable=True)
    total_sugar_g = Column(Float, nullable=True)
    class_name = Column(String(255), nullable=True)
    subclass_name = Column(String(255), nullable=True)
    nova = Column(Text, nullable=True)
    octagon_count = Column(Integer, nullable=False, default=0)

    user_id = Column(String(100), nullable=True, index=True)
    location = Column(String(255), nullable=True)
    image_path = Column(String(500), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
