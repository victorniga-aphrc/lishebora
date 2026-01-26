"""SQLAlchemy database models."""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.orm import relationship

from app.db import Base

# Association table for many-to-many relationship between products and ingredients
product_ingredient_association = Table(
    "product_ingredients",
    Base.metadata,
    Column("product_id", Integer, ForeignKey("products.id"), primary_key=True),
    Column("ingredient_id", Integer, ForeignKey("ingredients.id"), primary_key=True),
)


class Product(Base):
    """Product information."""

    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=True, index=True)
    brand = Column(String(255), nullable=True, index=True)
    category = Column(String(100), nullable=True, index=True)
    barcode = Column(String(50), nullable=True, unique=True, index=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    ingredients = relationship(
        "Ingredient",
        secondary=product_ingredient_association,
        back_populates="products",
    )
    nutrition_data = relationship("NutritionData", back_populates="product", uselist=False)
    scans = relationship("Scan", back_populates="product")


class Ingredient(Base):
    """Ingredient information."""

    __tablename__ = "ingredients"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, unique=True, index=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    products = relationship(
        "Product",
        secondary=product_ingredient_association,
        back_populates="ingredients",
    )


class NutritionData(Base):
    """Nutrition information per 100g/100ml for a product."""

    __tablename__ = "nutrition_data"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), unique=True, nullable=False, index=True)
    
    # Core KNPM nutrients
    energy_kcal = Column(Float, nullable=True)
    total_fat = Column(Float, nullable=True)
    saturated_fat = Column(Float, nullable=True)
    trans_fat = Column(Float, nullable=True)
    total_sugar = Column(Float, nullable=True)
    sodium = Column(Float, nullable=True)
    protein = Column(Float, nullable=True)
    carbohydrates = Column(Float, nullable=True)
    fiber = Column(Float, nullable=True)
    
    # Additional nutrients stored as JSONB for flexibility
    additional_nutrients = Column(JSON, nullable=True, default=dict)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    product = relationship("Product", back_populates="nutrition_data")


class Scan(Base):
    """Scan event - tracks when a product was scanned."""

    __tablename__ = "scans"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True, index=True)
    
    # Scan metadata
    user_id = Column(String(100), nullable=True, index=True)  # For future authentication
    location = Column(String(255), nullable=True)  # County/city for analytics
    image_path = Column(String(500), nullable=True)  # Path to stored image (optional)
    
    # Extraction metadata
    ingredients_found = Column(Boolean, default=False, nullable=False)
    nutrition_facts_found = Column(Boolean, default=False, nullable=False)
    product_name_found = Column(Boolean, default=False, nullable=False)
    barcode_found = Column(Boolean, default=False, nullable=False)
    
    # Raw data for debugging/research
    raw_text = Column(Text, nullable=True)
    model_raw_output = Column(JSON, nullable=True)
    
    # Warnings and errors
    warnings = Column(JSON, nullable=True, default=list)
    errors = Column(JSON, nullable=True, default=list)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Relationships
    product = relationship("Product", back_populates="scans")
