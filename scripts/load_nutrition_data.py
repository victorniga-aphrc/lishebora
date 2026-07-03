"""Load nutrition data from cleaned CSVs into PostgreSQL catalog tables.

This script is idempotent and safe to run multiple times:
- Existing data is TRUNCATED and replaced (not appended)
- Validates CSV structure before loading
- Provides detailed progress and error reporting

Usage:
    python scripts/load_nutrition_data.py [--env-file .env]

Environment variables required (from .env):
    DATABASE_URL - PostgreSQL connection string

Tables populated:
    - catalog.product_nutrition (from all_categories_nutrients_classified.csv)
    - catalog.food_composition_reference (from food_reference_nutrients_classified.csv)
"""

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Optional

try:
    import psycopg2
    from psycopg2.extras import execute_batch
except ImportError:
    print("Error: psycopg2 not installed. Run: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(1)

try:
    from dotenv import load_dotenv
except ImportError:
    print("Warning: python-dotenv not installed. Relying on environment variables only.")
    load_dotenv = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
STAGED = ROOT / "data_database" / "staged"

PRIMARY_CSV = STAGED / "all_categories_nutrients_classified.csv"
REFERENCE_CSV = STAGED / "food_reference_nutrients_classified.csv"

EXPECTED_COLUMNS_PRIMARY = ["Food Name", "Sugar", "Fat", "Sodium", "class_name", "subclass_name", "nova", "octagons"]
EXPECTED_COLUMNS_REFERENCE = ["Food Name", "Sugar", "Fat", "Sodium", "class_name", "subclass_name", "nova"]


def parse_nutrient(value: str) -> Optional[float]:
    """Parse nutrient value like '17g', '0.5g' to float, or None if empty."""
    if not value or not isinstance(value, str):
        return None
    value = value.strip().lower().rstrip('g').strip()
    if not value or value == "0":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def validate_csv(csv_path: Path, expected_columns: list[str]) -> None:
    """Validate CSV exists and has expected columns."""
    if not csv_path.is_file():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")
    
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"{csv_path.name} has no header row")
        
        actual = set(reader.fieldnames)
        expected = set(expected_columns)
        
        if not expected.issubset(actual):
            missing = expected - actual
            raise ValueError(f"{csv_path.name} missing columns: {missing}")


def load_product_nutrition(conn, csv_path: Path) -> int:
    """Load primary product nutrition data into catalog.product_nutrition."""
    print(f"\nLoading {csv_path.name} -> catalog.product_nutrition...")
    
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    
    if not rows:
        print("  Warning: CSV is empty, skipping.")
        return 0
    
    # Prepare data for batch insert
    records = []
    for r in rows:
        food_name = (r.get("Food Name") or "").strip()
        if not food_name:
            continue
        
        class_name = (r.get("class_name") or "").strip()
        subclass_name = (r.get("subclass_name") or "").strip()
        if not class_name or not subclass_name:
            print(f"  Warning: Skipping row with empty classification: {food_name}")
            continue
        
        records.append((
            food_name,
            parse_nutrient(r.get("Sugar", "")),
            parse_nutrient(r.get("Fat", "")),
            parse_nutrient(r.get("Sodium", "")),
            class_name,
            subclass_name,
            (r.get("nova") or "").strip() or None,
            int(r.get("octagons", 0) or 0),
        ))
    
    if not records:
        print("  Warning: No valid records to insert.")
        return 0
    
    # Truncate and reload (idempotent)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE catalog.product_nutrition RESTART IDENTITY CASCADE")
        
        execute_batch(
            cur,
            """
            INSERT INTO catalog.product_nutrition 
                (food_name, sugar_g, fat_g, sodium_g, class_name, subclass_name, nova, octagon_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            records,
            page_size=1000,
        )
        conn.commit()
    
    print(f"  OK Loaded {len(records)} products")
    return len(records)


def load_food_composition_reference(conn, csv_path: Path) -> int:
    """Load food composition reference data into catalog.food_composition_reference."""
    print(f"\nLoading {csv_path.name} -> catalog.food_composition_reference...")
    
    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    
    if not rows:
        print("  Warning: CSV is empty, skipping.")
        return 0
    
    # Prepare data for batch insert
    records = []
    for r in rows:
        food_name = (r.get("Food Name") or "").strip()
        if not food_name:
            continue
        
        class_name = (r.get("class_name") or "").strip()
        subclass_name = (r.get("subclass_name") or "").strip()
        if not class_name or not subclass_name:
            print(f"  Warning: Skipping row with empty classification: {food_name}")
            continue
        
        records.append((
            food_name,
            parse_nutrient(r.get("Sugar", "")),
            parse_nutrient(r.get("Fat", "")),
            parse_nutrient(r.get("Sodium", "")),
            class_name,
            subclass_name,
            (r.get("nova") or "").strip() or None,
        ))
    
    if not records:
        print("  Warning: No valid records to insert.")
        return 0
    
    # Truncate and reload (idempotent)
    with conn.cursor() as cur:
        cur.execute("TRUNCATE TABLE catalog.food_composition_reference RESTART IDENTITY CASCADE")
        
        execute_batch(
            cur,
            """
            INSERT INTO catalog.food_composition_reference 
                (food_name, sugar_g, fat_g, sodium_g, class_name, subclass_name, nova)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            records,
            page_size=1000,
        )
        conn.commit()
    
    print(f"  OK Loaded {len(records)} reference foods")
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(description="Load nutrition data CSVs into PostgreSQL")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ROOT / ".env",
        help="Path to .env file (default: .env in project root)",
    )
    args = parser.parse_args()
    
    # Load environment
    if load_dotenv and args.env_file.is_file():
        load_dotenv(args.env_file)
        print(f"Loaded environment from {args.env_file}")
    
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable not set. Check your .env file.")
    
    print(f"\nConnecting to PostgreSQL...")
    print(f"  Database: {database_url.split('@')[-1]}")  # Show host/db part only
    
    # Validate CSVs exist and have correct structure
    print("\nValidating CSV files...")
    validate_csv(PRIMARY_CSV, EXPECTED_COLUMNS_PRIMARY)
    print(f"  OK {PRIMARY_CSV.name}")
    validate_csv(REFERENCE_CSV, EXPECTED_COLUMNS_REFERENCE)
    print(f"  OK {REFERENCE_CSV.name}")
    
    # Connect and load data
    conn = psycopg2.connect(database_url)
    try:
        total_products = load_product_nutrition(conn, PRIMARY_CSV)
        total_reference = load_food_composition_reference(conn, REFERENCE_CSV)
        
        # Verify counts
        print("\nVerifying database counts...")
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM catalog.product_nutrition")
            db_products = cur.fetchone()[0]
            
            cur.execute("SELECT COUNT(*) FROM catalog.food_composition_reference")
            db_reference = cur.fetchone()[0]
        
        print(f"  catalog.product_nutrition:           {db_products:,} rows")
        print(f"  catalog.food_composition_reference:  {db_reference:,} rows")
        
        if db_products != total_products or db_reference != total_reference:
            print("\nWARNING: Row count mismatch detected!")
        else:
            print("\nOK: Data load complete and verified!")
        
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
