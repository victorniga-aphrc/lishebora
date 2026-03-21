# Changelog

All notable changes to this project will be documented in this file.

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

