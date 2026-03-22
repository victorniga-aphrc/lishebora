# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added
- **Documentation** — `docs/PIPELINE.md`: end-to-end `/extract` flow with Mermaid diagrams, nutrients vs KNPM **thresholds** distinction, data files, env vars, and code map; README TOC and “How It Works” link to it.
- **KNPM category thresholds** — `data/knpm_category_threshold.csv` drives per-category “high in” limits (not product nutrition). `knpm_category_thresholds.py` resolves a row from OCR + POS hints (fuzzy on `category_name`) or defaults to **6.0 Composite foods**; `knpm_labeller` uses row limits with `null` = skip that nutrient. `knpm_label` gains `knpm_category_*` and `knpm_thresholds_source`. Env: `KNPM_CATEGORY_THRESHOLD_CSV`, `KNPM_CATEGORY_FUZZY_MIN_SCORE`. POS lookup runs **before** KNPM so hints include subclass/class.
- **KNPM category resolution (fix)**: Multiple hint variants (POS-only, OCR-only, combined), **max(token_set_ratio, partial_ratio, WRatio)** scorer for long MoH names vs short POS lines, **`csv_pos_class_bridge`** mapping (e.g. POS `BREADS` → KNPM **2.2**), and `resolve_knpm_thresholds_for_extraction()`.
- **Vision product-type hint for POS taxonomy** — OCR JSON includes optional `product_info.visual_product_type` (plain-English type from packaging when text is weak). `supermarket_lookup` taxonomy fallback tries **category**, **visual_product_type**, and **combined** strings and keeps the best fuzzy match (`taxonomy_*_from_category` / `_from_visual_product_type` / `_from_combined`).
- **Reference nutrition** — `scripts/build_reference_nutrition_lookup.py` produces `data/reference_nutrition_lookup.csv` from `all_categories_combined.csv` (column trim, numeric parse, name normalization, per-X→100g scaling, dedupe). Output columns: `product_name`, nutrients, `sub_type`, `form` only (no basis/portion/category/flavour columns).
- **Reference nutrition on `/extract`** — When the model returns **no usable numeric** per-100g data, `lookup_reference_nutrition` (`app/services/reference_nutrition_lookup.py`) matches `product_info` to that CSV (exact/fuzzy, same normalization as POS). Fills `nutrition_per_100g`, sets `reference_nutrition_match`, `extraction_metadata.nutrition_from_reference_lookup`, warning text, and persists match info in `Scan.model_raw_output`. Env: `REFERENCE_NUTRITION_LOOKUP_CSV`, `REFERENCE_NUTRITION_FUZZY_MIN_SCORE`, `REFERENCE_NUTRITION_LOOKUP_ENABLED`.
- **POS vs label sugar consistency** — When `supermarket_classification` text suggests no/low added sugar but KNPM flags `HIGH_IN_SUGAR`, a **`warnings`** entry is appended (`classification_consistency.py`) so responses are not read as contradictory without explanation.
- **Supermarket POS taxonomy on extract** — `OcrResult.supermarket_classification` (`class_name`, `subclass_name`, `nova`, `matched_description`, `match_method`, `match_score`) from `data/product_class_subclass_lookup.csv` via `app/services/supermarket_lookup.py` (exact + fuzzy with `rapidfuzz`).
- **Config** — `SUPERMARKET_*`, `REFERENCE_NUTRITION_*`, `KNPM_CATEGORY_THRESHOLD_CSV`, `KNPM_CATEGORY_FUZZY_MIN_SCORE` (see README env table).
- **Persistence** — `save_ocr_result_to_db` merges `supermarket_classification`, `reference_nutrition_match`, and `nutrition_from_reference_lookup` into `Scan.model_raw_output` when present.
- **Demo** — “Supermarket taxonomy (POS)” card on `/`.
- **`OcrResult` JSON** — Top-level **`class_name`** and **`subclass_name`** (mirrored from `supermarket_classification`) for easier clients; persisted on scans when applicable.

### Changed
- **`normalize_pack_description`** — Extra stripping for glued sizes (e.g. OIL500G, POUCH200ML), reverse multipacks (500MLX6, 1LX6, 1.5LX6), codes like G14G (before glued-`G` rules), and apostrophe pack counts (10'S); used for POS, reference nutrition build, and runtime matching.
- Fixed undefined `has_trans_fats` / `has_sweeteners` when the model returns no ingredients (KNPM path).
- **Supermarket lookup**: SKU fuzzy matching now uses **max(WRatio, partial_ratio)** so OCR names like “Orchid Valley Delight” match long POS descriptions; **category / visual_product_type / combined → taxonomy** fallback when no SKU line matches (`taxonomy_subclass_from_*` / `taxonomy_class_from_*`).
- **Lookup CSV build**: `build_product_classification_lookup.py` strips **pack sizes / pack types** from POS descriptions, then dedupes (**~1.2k** rows from 10k lines in current data). Shared **`app/utils/pos_description.normalize_pack_description`** used at build time and in **`supermarket_lookup`** for OCR queries (extended for **G/**, **PCS**, **\* V/**, sachet **N\*MG/**, **N\* FLAVOUR**, trailing counts, **GLASS**/CTN/SATCHETS/POUCH, **PK**, **/KG**, **NS** counts, **14S/**, **J/SUPER**, trailing `5*`, empty **( )**, **/ (6S)**, space-slash-space, **punctuation** (`.`, `,`, trailing stops), standalone **PL**, **EOT** / spaced **E O T**, **CHOC**/**BISC** expansions, **CT** / **M/B** / **M/BAK(ERS)** removal, etc.).

---

## [0.5.0] - 2026-02-12

### Added
- **OpenAI OCR integration**
  - Switched from Replicate to direct **OpenAI** client (vision‑enabled GPT‑4.x mini models) for label OCR and structured extraction.
  - Support for image input via base64 data URLs in the OCR pipeline.
- **KNPM labeller (first version)**
  - New `knpm_labeller` service that:
    - Uses simplified KNPM thresholds for sugar, fat, saturated fat and sodium (per 100g) to classify products.
    - Produces a `knpm_label` block in the API response with:
      - `classification` (`FIT_FOR_CONSUMPTION`, `LESS_HEALTHY`, `UNKNOWN`)
      - `octagons` (e.g. `HIGH_IN_SUGAR`, `HIGH_IN_SALT`, `HIGH_IN_FAT`)
      - detailed `reasons` and an optional `message`.
  - Proper handling of **no nutrition data** → `classification="UNKNOWN"` with an explicit message.
- **Demo UI KNPM visuals**
  - Added a KNPM card to the demo page that:
    - Shows a **green octagon with a tick** for “Fit for consumption”.
    - Shows one to three **black octagons** labelled “High in Sugar”, “High in Salt”, “High in Fat” when thresholds are exceeded.
  - Improved JSON output panel (wrapped lines, vertical scrolling) for better readability.

### Changed
- Updated `OcrResult` to include a `knpm_label` field.
- Cleaned up KNPM reason text (removed “demo” wording) and simplified status text in the UI.

---

## [0.4.0] - 2026-02-12

### Added
- **Mobile & demo UX**
  - Camera capture option on the demo page (`/`) so users can upload from files or capture directly from device camera.
  - Documentation on accessing the app from a phone on the same LAN (including WSL and IP/port guidance).
- **AWS / EC2 deployment tooling**
  - `aws/` helpers (credentials loader, SSM-based deploy, EC2 deployment README) for deploying to an existing EC2 instance without SSH keys.
  - `aws/run-deploy-with-log.sh` to run SSM deploy and persist logs for debugging.
- **Database on EC2**
  - Extended `DATABASE_SETUP.md` with EC2/Amazon Linux instructions (PostgreSQL service, custom port when 5432 is already used, `pg_hba.conf` tuning).

### Changed
- Clarified Docker usage for EC2 (avoid port clashes with existing Postgres, optional API port remap).
- Added `.gitattributes` to enforce LF line endings for shell scripts and reduce CRLF-related issues in WSL/EC2 environments.

---

## [0.3.0] - 2026-01-25

### Added
- **Database integration**
  - SQLAlchemy models for `products`, `ingredients`, `nutrition_data`, `scans`, and association table `product_ingredients`.
  - Alembic configuration (`alembic.ini`, `alembic/env.py`, `alembic/versions/15b732399207_initial_migration.py`).
  - Automatic persistence of OCR results via `save_ocr_result_to_db` service.
- **Docker support**
  - `Dockerfile` for FastAPI application.
  - `docker-compose.yml` for app + PostgreSQL stack.
  - `.dockerignore` to optimize Docker build context.
- **Documentation**
  - `DATABASE_SETUP.md` with detailed DB setup and migration instructions.
  - `DOCKER_SETUP.md` with Docker usage, troubleshooting, and production notes.
  - Expanded `README.md` with Docker section and updated database status/version.
  - `WORKFLOW.md`, `WORKFLOW_DIAGRAM.md`, and `COMPLETE_WORKFLOW_DIAGRAM.md` updated to include database storage and future KNPM/recommendation steps.

### Changed
- Enhanced OCR pipeline output structure to include:
  - `nutrition_per_100g` with core KNPM nutrients and `additional_nutrients` dict.
  - `product_info` (name, brand, category, barcode).
  - `extraction_metadata`, `warnings`, and `errors` for robust handling of missing/partial data.
- Updated `OcrResult` and related parsing logic to support full nutrition extraction and richer metadata.

---

## [0.2.0] - 2026-01-24

### Added
- Complete nutrition extraction in OCR pipeline:
  - Core nutrients: energy, total fat, saturated fat, trans fat, total sugar, sodium, protein, carbohydrates, fiber.
  - Support for capturing all additional nutrients from the label (e.g., potassium, calcium, iron, vitamins) in `additional_nutrients`.
- Product information extraction (name, brand, category, barcode).
- Detection of trans fats and artificial sweeteners from ingredients.
- `WORKFLOW.md` and `WORKFLOW_DIAGRAM.md` to describe full ingredient + nutrition flow.

### Changed
- Prompting for GPT-4.1-mini via Replicate to return a richer JSON schema.
- Response cleaning and parsing to handle markdown fences and extract strict JSON.

---

## [0.1.0] - 2026-01-23

### Added
- Initial FastAPI backend with:
  - `/` demo page (HTML upload form + JS).
  - `/extract` endpoint accepting image uploads and returning structured OCR result.
  - `/health` endpoint for health checks.
- Basic OCR pipeline integration with Replicate (starting with GPT-4o-mini, then GPT-4.1-mini):
  - Image upload → OCR → ingredient list + raw text.
- Initial project documentation in `README.md`.
- Git workflow helper in `git_workflow.md`.

