"""SQLAlchemy models for catalog nutrition tables.

These models are read-only reference models for the nutrition catalog.
Data is loaded via scripts/load_nutrition_data.py, not through ORM inserts.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Identity, Integer, String, Text
from app.db import Base

CATALOG_SCHEMA = "catalog"


class ProductNutrition(Base):
    """
    Retail product nutrition data (3,973 SKUs) with KNPM octagon warnings.
    
    Loaded from: data_database/staged/all_categories_nutrients_classified.csv
    """

    __tablename__ = "product_nutrition"
    __table_args__ = {"schema": CATALOG_SCHEMA}

    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    food_name = Column(Text, nullable=False)
    sugar_g = Column(Float, nullable=True)
    fat_g = Column(Float, nullable=True)
    sodium_g = Column(Float, nullable=True)
    class_name = Column(Text, nullable=False)
    subclass_name = Column(Text, nullable=False)
    nova = Column(Text, nullable=True)
    octagon_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<ProductNutrition(id={self.id}, food_name={self.food_name!r}, octagons={self.octagon_count})>"


class FoodCompositionReference(Base):
    """
    Standard food composition reference data (654 foods) for nutrient lookup and imputation.
    
    Loaded from: data_database/staged/food_reference_nutrients_classified.csv
    """

    __tablename__ = "food_composition_reference"
    __table_args__ = {"schema": CATALOG_SCHEMA}

    id = Column(Integer, Identity(always=True), primary_key=True, index=True)
    food_name = Column(Text, nullable=False)
    sugar_g = Column(Float, nullable=True)
    fat_g = Column(Float, nullable=True)
    sodium_g = Column(Float, nullable=True)
    class_name = Column(Text, nullable=False)
    subclass_name = Column(Text, nullable=False)
    nova = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<FoodCompositionReference(id={self.id}, food_name={self.food_name!r})>"
