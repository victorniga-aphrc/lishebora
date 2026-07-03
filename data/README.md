# Data directory

| File | Role |
|------|------|
| `knpm_category_threshold.csv` | KNPM-style threshold reference (curated). |
| `product_class_subclass_lookup.csv` | Slim POS / taxonomy lookup (output of `scripts/build_product_classification_lookup.py`). |
| `reference_nutrition_lookup.csv` | Cleaned reference nutrition (output of `scripts/build_reference_nutrition_lookup.py`). |
| `reference_nutrition_lookup_simulated.csv` | Optional seed rows; load into **`catalog.reference_products`** via `scripts/load_simulated_reference_to_postgres.py` (uses `DATABASE_URL` / `REFERENCE_CATALOG_*`). |
| `substitute_recommender_test_products.csv` | Seed names for substitute recommender tests. |
| `supermarket_a_recode_file.xlsx` | Optional recode spreadsheet (if used by your ETL). |

**Not versioned (see root `.gitignore`):**

- `huge_data.csv` — large raw retailer export; use as `--source` for `scripts/build_product_classification_lookup.py` when present locally.
- `all_categories_combined.csv` — raw category export; use as `--source` for `scripts/build_reference_nutrition_lookup.py` when present locally.

Rebuild the committed CSVs from those sources when you update upstream data.
