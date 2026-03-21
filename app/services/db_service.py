"""Service for saving extracted data to the database."""

from typing import Optional

from sqlalchemy.orm import Session

from app.database.models import Ingredient, NutritionData, Product, Scan
from app.models import ExtractionMetadata, OcrResult


def get_or_create_ingredient(db: Session, ingredient_name: str) -> Ingredient:
    """Get existing ingredient or create a new one."""
    ingredient = db.query(Ingredient).filter(Ingredient.name == ingredient_name).first()
    if not ingredient:
        ingredient = Ingredient(name=ingredient_name)
        db.add(ingredient)
        db.flush()  # Flush to get the ID without committing
    return ingredient


def save_ocr_result_to_db(
    db: Session,
    ocr_result: OcrResult,
    user_id: Optional[str] = None,
    location: Optional[str] = None,
    image_path: Optional[str] = None,
) -> tuple[Product, Scan]:
    """
    Save OCR extraction result to the database.
    
    Returns:
        Tuple of (Product, Scan) objects
    """
    # Step 1: Find or create product
    product = None
    
    # Try to find by barcode first
    if ocr_result.product_info and ocr_result.product_info.barcode:
        product = db.query(Product).filter(Product.barcode == ocr_result.product_info.barcode).first()
    
    # If not found by barcode, try to find by name and brand
    if not product and ocr_result.product_info and ocr_result.product_info.name:
        query = db.query(Product).filter(Product.name == ocr_result.product_info.name)
        if ocr_result.product_info.brand:
            query = query.filter(Product.brand == ocr_result.product_info.brand)
        product = query.first()
    
    # Create new product if not found
    if not product:
        product = Product(
            name=ocr_result.product_info.name if ocr_result.product_info else None,
            brand=ocr_result.product_info.brand if ocr_result.product_info else None,
            category=ocr_result.product_info.category if ocr_result.product_info else None,
            barcode=ocr_result.product_info.barcode if ocr_result.product_info else None,
        )
        db.add(product)
        db.flush()  # Flush to get the ID
    
    # Step 2: Save ingredients and link to product
    if ocr_result.ingredients:
        for ingredient_obj in ocr_result.ingredients:
            ingredient = get_or_create_ingredient(db, ingredient_obj.name)
            if ingredient not in product.ingredients:
                product.ingredients.append(ingredient)
    
    # Step 3: Save nutrition data
    if ocr_result.nutrition_per_100g:
        # Check if nutrition data already exists for this product
        existing_nutrition = db.query(NutritionData).filter(
            NutritionData.product_id == product.id
        ).first()
        
        if existing_nutrition:
            # Update existing nutrition data
            existing_nutrition.energy_kcal = ocr_result.nutrition_per_100g.energy_kcal
            existing_nutrition.total_fat = ocr_result.nutrition_per_100g.total_fat
            existing_nutrition.saturated_fat = ocr_result.nutrition_per_100g.saturated_fat
            existing_nutrition.trans_fat = ocr_result.nutrition_per_100g.trans_fat
            existing_nutrition.total_sugar = ocr_result.nutrition_per_100g.total_sugar
            existing_nutrition.sodium = ocr_result.nutrition_per_100g.sodium
            existing_nutrition.protein = ocr_result.nutrition_per_100g.protein
            existing_nutrition.carbohydrates = ocr_result.nutrition_per_100g.carbohydrates
            existing_nutrition.fiber = ocr_result.nutrition_per_100g.fiber
            existing_nutrition.additional_nutrients = ocr_result.nutrition_per_100g.additional_nutrients
        else:
            # Create new nutrition data
            nutrition_data = NutritionData(
                product_id=product.id,
                energy_kcal=ocr_result.nutrition_per_100g.energy_kcal,
                total_fat=ocr_result.nutrition_per_100g.total_fat,
                saturated_fat=ocr_result.nutrition_per_100g.saturated_fat,
                trans_fat=ocr_result.nutrition_per_100g.trans_fat,
                total_sugar=ocr_result.nutrition_per_100g.total_sugar,
                sodium=ocr_result.nutrition_per_100g.sodium,
                protein=ocr_result.nutrition_per_100g.protein,
                carbohydrates=ocr_result.nutrition_per_100g.carbohydrates,
                fiber=ocr_result.nutrition_per_100g.fiber,
                additional_nutrients=ocr_result.nutrition_per_100g.additional_nutrients,
            )
            db.add(nutrition_data)
    
    # Step 4: Create scan record (enrich persisted model output with supermarket taxonomy)
    raw_out = ocr_result.model_raw_output
    if isinstance(raw_out, dict):
        merged_raw: dict = {**raw_out}
    elif raw_out is not None:
        merged_raw = {"_legacy_model_raw_output": raw_out}
    else:
        merged_raw = {}
    if ocr_result.supermarket_classification is not None:
        merged_raw["supermarket_classification"] = (
            ocr_result.supermarket_classification.model_dump()
        )

    if merged_raw:
        merged_raw["class_name"] = ocr_result.class_name
        merged_raw["subclass_name"] = ocr_result.subclass_name
        final_raw: dict | None = merged_raw
    elif ocr_result.class_name is not None or ocr_result.subclass_name is not None:
        final_raw = {
            "class_name": ocr_result.class_name,
            "subclass_name": ocr_result.subclass_name,
        }
    else:
        final_raw = None

    scan = Scan(
        product_id=product.id,
        user_id=user_id,
        location=location,
        image_path=image_path,
        ingredients_found=ocr_result.extraction_metadata.ingredients_found,
        nutrition_facts_found=ocr_result.extraction_metadata.nutrition_facts_found,
        product_name_found=ocr_result.extraction_metadata.product_name_found,
        barcode_found=ocr_result.extraction_metadata.barcode_found,
        raw_text=ocr_result.raw_text,
        model_raw_output=final_raw,
        warnings=ocr_result.warnings,
        errors=ocr_result.errors,
    )
    db.add(scan)
    
    # Commit all changes
    db.commit()
    
    # Refresh to get updated relationships
    db.refresh(product)
    db.refresh(scan)
    
    return product, scan
