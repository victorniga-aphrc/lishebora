

# Database Deployment Guide

This guide covers deploying the nutrition catalog tables to production.

## Overview

The nutrition catalog consists of two PostgreSQL tables in the `catalog` schema:

1. **`catalog.product_nutrition`** (3,973 rows)
   - Retail product SKUs with nutrition data and KNPM octagon warnings
   - Source: `data_database/staged/all_categories_nutrients_classified.csv`
   - Includes: sugar, fat, sodium, food classification, and octagon_count (0-3)

2. **`catalog.food_composition_reference`** (654 rows)
   - Standard food composition reference data, used as a SECONDARY FALLBACK
     when a scanned product cannot be matched in `product_nutrition`
   - Source: `data_database/staged/food_reference_nutrients_classified.csv`
   - Includes: fat, sodium, food classification (no sugar in source data, no octagons)

### Lookup workflow

1. **Primary**: try exact, then fuzzy name match in `catalog.product_nutrition`.
   If a row is returned, that row's nutrients are used as-is (NULLs stay NULL).
2. **Secondary fallback** (only when primary completely missed): try fuzzy name
   match in `catalog.food_composition_reference`. This catches generic foods
   ("Cod fillet raw", "Maize Porridge", etc.) that don't appear as retail SKUs.
3. (Optional, deeper fallback) `lookup_food_composition_by_classification()` can
   be called by callers who already have a class/subclass to get category averages.

## Database Schema

```
catalog
├── product_nutrition
│   ├── id (SERIAL PRIMARY KEY)
│   ├── food_name (TEXT NOT NULL)
│   ├── sugar_g (DOUBLE PRECISION NULL)
│   ├── fat_g (DOUBLE PRECISION NULL)
│   ├── sodium_g (DOUBLE PRECISION NULL)
│   ├── class_name (TEXT NOT NULL)
│   ├── subclass_name (TEXT NOT NULL)
│   ├── nova (TEXT NULL)
│   ├── octagon_count (INTEGER NOT NULL DEFAULT 0)
│   ├── created_at (TIMESTAMPTZ NOT NULL DEFAULT NOW())
│   └── updated_at (TIMESTAMPTZ NOT NULL DEFAULT NOW())
│
└── food_composition_reference
    ├── id (SERIAL PRIMARY KEY)
    ├── food_name (TEXT NOT NULL)
    ├── sugar_g (DOUBLE PRECISION NULL)
    ├── fat_g (DOUBLE PRECISION NULL)
    ├── sodium_g (DOUBLE PRECISION NULL)
    ├── class_name (TEXT NOT NULL)
    ├── subclass_name (TEXT NOT NULL)
    ├── nova (TEXT NULL)
    ├── created_at (TIMESTAMPTZ NOT NULL DEFAULT NOW())
    └── updated_at (TIMESTAMPTZ NOT NULL DEFAULT NOW())
```

### Indexes

Both tables have optimized indexes for common queries:
- Full-text search on `food_name` (GIN index with tsvector)
- Composite index on `(class_name, subclass_name)`
- Index on `nova` classification
- Unique index on `LOWER(TRIM(food_name))` for fuzzy matching
- `product_nutrition` also has index on `octagon_count`

## Deployment Steps

### 1. Prerequisites

Ensure you have:
- PostgreSQL 12+ running
- Python 3.10+ with required packages: `psycopg2-binary`, `python-dotenv`
- Access to the `.env` file with `DATABASE_URL`
- Latest CSV files in `data_database/staged/`

Install dependencies:
```bash
pip install psycopg2-binary python-dotenv alembic sqlalchemy
```

### 2. Run Alembic Migration

The migration creates the new tables and drops the old `catalog.reference_products` table.

```bash
# Check current migration status
alembic current

# Run the migration
alembic upgrade head

# Verify migration applied
alembic current
# Should show: d8e9f0a1b2c3 (head)
```

**What the migration does:**
- Creates `catalog` schema (if not exists)
- Drops old `catalog.reference_products` table
- Creates `catalog.product_nutrition` with indexes
- Creates `catalog.food_composition_reference` with indexes

### 3. Load Data from CSVs

Run the data loader script to populate the tables:

```bash
python scripts/load_nutrition_data.py
```

**What this script does:**
- Validates CSV files exist and have correct columns
- Connects to PostgreSQL using `DATABASE_URL` from `.env`
- **Truncates existing data** (idempotent - safe to run multiple times)
- Loads products from `all_categories_nutrients_classified.csv`
- Loads reference foods from `food_reference_nutrients_classified.csv`
- Verifies row counts match expectations

**Expected output:**
```
Loaded environment from .env
Connecting to PostgreSQL...
  Database: 127.0.0.1:5432/supermarket_a

Validating CSV files...
  ✓ all_categories_nutrients_classified.csv
  ✓ food_reference_nutrients_classified.csv

Loading all_categories_nutrients_classified.csv → catalog.product_nutrition...
  ✓ Loaded 3973 products

Loading food_reference_nutrients_classified.csv → catalog.food_composition_reference...
  ✓ Loaded 654 reference foods

Verifying database counts...
  catalog.product_nutrition:           3,973 rows
  catalog.food_composition_reference:  654 rows

✓ Data load complete and verified!
```

### 4. Verify Deployment

Connect to PostgreSQL and verify:

```sql
-- Check table existence
\dt catalog.*

-- Verify row counts
SELECT 'product_nutrition' AS table, COUNT(*) AS rows 
FROM catalog.product_nutrition
UNION ALL
SELECT 'food_composition_reference', COUNT(*) 
FROM catalog.food_composition_reference;

-- Sample data from product_nutrition
SELECT food_name, sugar_g, fat_g, sodium_g, octagon_count, class_name, subclass_name
FROM catalog.product_nutrition
WHERE octagon_count = 3
LIMIT 5;

-- Sample data from food_composition_reference
SELECT food_name, sugar_g, fat_g, sodium_g, class_name, subclass_name
FROM catalog.food_composition_reference
WHERE class_name = 'PULSES - PEAS, LENTILS, BEANS'
LIMIT 5;

-- Test full-text search
SELECT food_name, class_name, subclass_name
FROM catalog.product_nutrition
WHERE to_tsvector('english', food_name) @@ to_tsquery('english', 'chocolate')
LIMIT 5;
```

## Production Deployment

### Option A: Manual Deployment

1. **Backup production database** (critical!)
   ```bash
   pg_dump -h <prod-host> -U <user> -d <database> -Fc -f backup_$(date +%Y%m%d_%H%M%S).dump
   ```

2. **Copy CSV files to production server**
   ```bash
   scp data_database/staged/*.csv user@production:/path/to/project/data_database/staged/
   ```

3. **Run migration on production**
   ```bash
   ssh user@production
   cd /path/to/project
   alembic upgrade head
   ```

4. **Load data on production**
   ```bash
   python scripts/load_nutrition_data.py
   ```

### Option B: Automated CI/CD

Add to your deployment pipeline:

```yaml
# Example GitHub Actions / GitLab CI
deploy_database:
  steps:
    - name: Backup production DB
      run: pg_dump ... -f backup.dump
    
    - name: Run Alembic migrations
      run: alembic upgrade head
      env:
        DATABASE_URL: ${{ secrets.PROD_DATABASE_URL }}
    
    - name: Load nutrition data
      run: python scripts/load_nutrition_data.py
      env:
        DATABASE_URL: ${{ secrets.PROD_DATABASE_URL }}
    
    - name: Verify deployment
      run: psql $DATABASE_URL -c "SELECT COUNT(*) FROM catalog.product_nutrition;"
```

## Updating Data

To update the nutrition data with new CSV files:

1. Replace the CSV files in `data_database/staged/`
2. Re-run the data loader: `python scripts/load_nutrition_data.py`
3. The script automatically truncates and reloads (idempotent)

**No migration needed** for data updates - only schema changes require new migrations.

## Rollback

If you need to rollback the migration:

```bash
# Rollback to previous migration
alembic downgrade a3b4c5d6e7f8
```

**Warning:** This will:
- Drop `catalog.product_nutrition` and `catalog.food_composition_reference`
- Recreate the old `catalog.reference_products` structure (empty)
- **Data is not restored automatically** - you must restore from backup

To restore data after rollback:
```bash
pg_restore -h <host> -U <user> -d <database> backup.dump
```

## Troubleshooting

### Migration fails with "table already exists"

Run the migration with the `--sql` flag to inspect what it would do:
```bash
alembic upgrade head --sql
```

Or manually drop the tables:
```sql
DROP TABLE IF EXISTS catalog.product_nutrition CASCADE;
DROP TABLE IF EXISTS catalog.food_composition_reference CASCADE;
```

Then re-run: `alembic upgrade head`

### Data loader fails with "database connection error"

Check your `.env` file has correct `DATABASE_URL`:
```bash
DATABASE_URL=postgresql://username:password@host:port/database
```

Test connection:
```bash
psql $DATABASE_URL -c "SELECT version();"
```

### Row count mismatch after loading

Check for:
- Rows with empty `class_name` or `subclass_name` (skipped by loader)
- CSV encoding issues (should be UTF-8)
- Duplicate food names (unique constraint violation)

View skipped rows in loader output or check CSV manually.

## Related Files

- **Migration**: `alembic/versions/d8e9f0a1b2c3_new_nutrition_tables.py`
- **Data Loader**: `scripts/load_nutrition_data.py`
- **Models**: `app/database/nutrition_models.py`
- **Source CSVs**: 
  - `data_database/staged/all_categories_nutrients_classified.csv`
  - `data_database/staged/food_reference_nutrients_classified.csv`
- **Pipeline**: `scripts/postgres_data_pipeline.py`

## Support

For issues or questions:
1. Check this deployment guide
2. Review migration file comments
3. Run loader script with `--help`: `python scripts/load_nutrition_data.py --help`
4. Check Alembic logs: `alembic history -v`
