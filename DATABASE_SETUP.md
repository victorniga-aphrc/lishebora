# Database Setup Guide

This guide explains how to set up and use the PostgreSQL database for the Lishebora backend.

## ✅ Current Status

**Database is fully configured and operational!**
- ✅ PostgreSQL database `lishebora` created
- ✅ All tables created via Alembic migrations
- ✅ Database connection configured in `.env`
- ✅ Automatic data storage on every scan working

This guide is for reference or setting up on a new machine.

## Prerequisites

- PostgreSQL installed and running
- Python dependencies installed (`pip install -r requirements.txt`)

## Database Configuration

### 1. Create PostgreSQL Database

```bash
# Connect to PostgreSQL (using TCP/IP to force password auth)
psql -h localhost -U postgres

# Create database
CREATE DATABASE lishebora;

# Set password for postgres user (if not already set)
ALTER USER postgres PASSWORD 'postgres';

# Exit psql
\q
```

**Note**: If you get "Peer authentication failed", use `psql -h localhost -U postgres` to force TCP/IP connection which uses password authentication.

### 2. Set Environment Variable

Add to your `.env` file:

```bash
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/lishebora
```

**Format**: `postgresql://username:password@host:port/database_name`

**Important**: Include the password in the connection string if you set one (as shown above).

**Default** (if not set): `postgresql://postgres@localhost:5432/lishebora` (no password)

## Database Migrations

### Initial Setup (✅ Completed)

1. **Create initial migration**:
   ```bash
   alembic revision --autogenerate -m "Initial migration"
   ```
   ✅ Migration file created: `alembic/versions/15b732399207_initial_migration.py`

2. **Apply migrations**:
   ```bash
   alembic upgrade head
   ```
   ✅ All tables created successfully:
   - `products`
   - `ingredients`
   - `product_ingredients`
   - `nutrition_data`
   - `scans`

**Status**: Database is fully set up and ready to use!

### Verify Database Setup

To verify everything is working:

```bash
# Check database connection
psql -h localhost -U postgres -d lishebora -c "\dt"

# Should show all tables:
# products, ingredients, product_ingredients, nutrition_data, scans

# Check current Alembic revision
alembic current

# Should show: 15b732399207 (head)
```

### Common Migration Commands

- **Create a new migration**: `alembic revision --autogenerate -m "Description"`
- **Apply migrations**: `alembic upgrade head`
- **Rollback one migration**: `alembic downgrade -1`
- **View current revision**: `alembic current`
- **View migration history**: `alembic history`

## Database Schema

### Tables

1. **`products`**: Product information (name, brand, category, barcode)
2. **`ingredients`**: Ingredient names (unique)
3. **`product_ingredients`**: Many-to-many relationship between products and ingredients
4. **`nutrition_data`**: Nutrition information per product (core nutrients + additional nutrients as JSON)
5. **`scans`**: Scan events (tracks when products were scanned)

### Relationships

- **Product** ↔ **Ingredients**: Many-to-many (via `product_ingredients`)
- **Product** → **NutritionData**: One-to-one
- **Product** → **Scans**: One-to-many

## How Data is Stored

When a user scans a product:

1. **Product is created/found**:
   - First tries to find by barcode
   - If not found, tries to find by name + brand
   - If still not found, creates new product

2. **Ingredients are saved**:
   - Each ingredient is stored once (unique by name)
   - Linked to product via association table

3. **Nutrition data is saved**:
   - Core KNPM nutrients stored as columns
   - Additional nutrients stored in JSONB `additional_nutrients` field
   - Updates existing nutrition data if product already exists

4. **Scan record is created**:
   - Links to product
   - Stores extraction metadata (what was found)
   - Stores warnings/errors
   - Stores raw text and model output for debugging

## Querying Data

### Example: Get product with nutrition data

```python
from app.db import SessionLocal
from app.database.models import Product

db = SessionLocal()
product = db.query(Product).filter(Product.barcode == "1234567890123").first()

if product:
    print(f"Product: {product.name}")
    print(f"Ingredients: {[ing.name for ing in product.ingredients]}")
    if product.nutrition_data:
        print(f"Sugar: {product.nutrition_data.total_sugar}g per 100g")
        print(f"Additional nutrients: {product.nutrition_data.additional_nutrients}")
```

### Example: Get all scans for analytics

```python
from app.database.models import Scan
from datetime import datetime, timedelta

# Get scans from last 24 hours
yesterday = datetime.utcnow() - timedelta(days=1)
recent_scans = db.query(Scan).filter(Scan.created_at >= yesterday).all()
```

## Troubleshooting

### Connection Errors

- **Check PostgreSQL is running**: `pg_isready`
- **Verify database exists**: `psql -l | grep lishebora`
- **Check credentials**: Verify username/password in `DATABASE_URL`

### Migration Issues

- **Reset database** (⚠️ **WARNING**: Deletes all data):
  ```bash
  # Drop and recreate database
  psql -U postgres -c "DROP DATABASE lishebora;"
  psql -U postgres -c "CREATE DATABASE lishebora;"
  
  # Re-run migrations
  alembic upgrade head
  ```

### Common Errors

- **"relation does not exist"**: Run migrations (`alembic upgrade head`)
- **"duplicate key value"**: Product/ingredient already exists (this is normal, handled automatically)
- **"connection refused"**: PostgreSQL not running or wrong host/port

## Production Considerations

1. **Use connection pooling**: Already configured in `app/db.py`
2. **Backup regularly**: Use `pg_dump` for backups
3. **Monitor performance**: Use PostgreSQL's `EXPLAIN ANALYZE` for slow queries
4. **Index optimization**: Indexes are already set on frequently queried fields (barcode, name, brand, category)

## Next Steps

- Add user authentication (link scans to users)
- Add image storage (save uploaded images to S3/local storage)
- Add analytics endpoints (query scan data)
- Add product search API
