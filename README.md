# Lishebora VIC Backend

**AI-Powered Food Label Ingredient Extraction API**

This repository contains the backend API for the Lishebora nutrition labelling tool—an AI-powered mobile application that helps Kenyan consumers make healthier food choices by scanning packaged foods and applying the Kenya Nutrient Profile Model (KNPM) for front-of-pack labeling.

## 📋 Table of Contents

- [Project Overview](#project-overview)
- [Current Status](#current-status)
- [Architecture](#architecture)
- [Quick Start](#quick-start)
- [Docker Setup](#docker-setup)
- [API Documentation](#api-documentation)
- [How It Works](#how-it-works)
- [Configuration](#configuration)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Next Steps](#next-steps)

---

## Project Overview

**Lishebora** is a research project led by APHRC (African Population and Health Research Center) that aims to:

1. **Extract ingredients** from food package images using OCR and AI
2. **Apply KNPM labeling** to classify products as "Fit for Consumption" or "Less Healthy" (black octagon warning)
3. **Recommend healthier alternatives** based on nutritional profiles
4. **Provide AI-generated explanations** to help consumers understand labeling decisions

This backend focuses on **Phase 1**: building a robust ingredient extraction pipeline that can read food labels from images and return structured, clean ingredient lists.

---

## Current Status

### ✅ What’s working (as of early 2026)

This section reflects **what you can run and use today** in this repo.

| Area | Status |
|------|--------|
| **HTTP API** | `GET /` (demo page), `POST /extract` (image → structured JSON), `GET /health`. Static assets: `/octagon_images/*` for warning SVGs. |
| **Vision OCR** | **OpenAI** (default `gpt-4.1-mini`) reads the label image and returns structured JSON—not the primary Replicate path anymore. (`OPENAI_API_KEY` required.) |
| **Ingredients** | Parsed list of ingredient names (no `confidence` field). |
| **Nutrition** | Per-100g fields where extractable: energy, fats (incl. saturated/trans), sugar, sodium, protein, carbs, fiber, plus `additional_nutrients` for anything else on the table. |
| **Product info** | Name, brand, category, barcode when visible. |
| **Metadata & safety** | `extraction_metadata`, `warnings`, `errors`, `raw_text`, `model_raw_output` for debugging. Keyword flags for trans fats / non-nutritive sweeteners in ingredients. |
| **KNPM labelling (v1)** | After extraction, **`knpm_label`** is attached: `FIT_FOR_CONSUMPTION`, `LESS_HEALTHY`, or **`UNKNOWN`** when there is no usable numeric nutrition. Multiple warnings: `HIGH_IN_SUGAR`, `HIGH_IN_SALT`, `HIGH_IN_FAT` with human-readable `reasons`. Thresholds are a simplified snack-oriented baseline (see `app/services/knpm_labeller.py`). |
| **Demo UI** | Upload **file** or **camera**; KNPM card with green “fit” octagon or black octagon SVGs; JSON panel with wrapping (no long horizontal scroll). Mobile-friendly with `--host 0.0.0.0` (see below). |
| **Database** | **PostgreSQL** + **SQLAlchemy** + **Alembic**. Each successful `/extract` can persist products, ingredients, nutrition rows, and scan records (`db_service`). |
| **Docker** | `Dockerfile` + `docker-compose.yml` for API + Postgres (see [DOCKER_SETUP.md](DOCKER_SETUP.md)). |
| **Test images** | `ingredient_image_data/` is intended to be **tracked in Git** for samples and experiments. |
| **Large local files** | `supermarket_a.backup` (and similar dumps) are **`.gitignore`d** so `git add` stays fast—do not commit multi‑GB backups. |
| **AWS / EC2** | Optional deploy helpers live under **`aws/`** (folder gitignored); see `aws/README.md` on your machine. |

### 🚧 Not built yet (planned)

- **Full KNPM product-type rules**: Current logic is a first pass; official category-specific thresholds and ministry rules still to be encoded.
- **Open Food Facts / Kenya Food Composition Tables**: No live validation against those databases yet.
- **Recommendation engine** and **GenAI explanations** for consumers.
- **Authentication**, user profiles, and analytics/search APIs over stored scans.

---

## Architecture

### Tech Stack

- **Framework**: FastAPI (Python 3.11+)
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Migrations**: Alembic
- **OCR/ML**: GPT-4.1-mini via **OpenAI** API (vision); Replicate env vars remain optional/legacy in `config.py`
- **Async Runtime**: Uvicorn with async/await
- **Data Models**: Pydantic for request/response validation, SQLAlchemy for database models
- **Configuration**: python-dotenv for environment variables

### System Flow

```
User Uploads Image
    ↓
FastAPI Endpoint (/extract)
    ↓
OCR Client Service (OpenAI vision)
    ↓
Response Cleaning & JSON Parsing
    ↓
KNPM Labeller (knpm_label on OcrResult)
    ↓
Save to PostgreSQL (db_service)
    ↓
JSON Response to Client (+ demo UI on GET /)
```

### Key Components

1. **`app/main.py`**: FastAPI application with routes
2. **`app/services/ocr_client.py`**: Core OCR logic using OpenAI (vision)
3. **`app/services/knpm_labeller.py`**: KNPM-style classification and octagon codes
4. **`app/services/db_service.py`**: Persists OCR results to PostgreSQL
5. **`app/models.py`**: Pydantic models (`OcrResult`, `KnpmLabel`, etc.)
6. **`app/database/models.py`**: SQLAlchemy ORM tables
7. **`app/db.py`**: Engine, sessions, `get_db`
8. **`app/config.py`**: Environment settings (`OPENAI_API_KEY`, `DATABASE_URL`, …)

---

## Quick Start

### Prerequisites

- Python 3.11 or higher
- PostgreSQL database (see [DATABASE_SETUP.md](DATABASE_SETUP.md) for setup instructions)
- **OpenAI API key** with access to a vision-capable model (default `gpt-4.1-mini`)
- Virtual environment (recommended)

### Installation

1. **Clone the repository** (if not already done)

```bash
cd /mnt/d/aphrc/lishebora_vic
```

2. **Create and activate a Python virtual environment**

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows PowerShell: .venv\Scripts\Activate.ps1
```

3. **Install dependencies**

```bash
pip install -r requirements.txt
```

4. **Set up environment variables**

Create a `.env` file in the project root:

```bash
# Required for OCR (vision)
OPENAI_API_KEY=sk-...
# Optional: override default model
# OPENAI_MODEL=gpt-4.1-mini

# Database configuration
DATABASE_URL=postgresql://postgres@localhost:5432/lishebora

# Legacy (not used by default): Replicate
# REPLICATE_API_TOKEN=...
```

5. **Set up database** (✅ Already completed)

The database is fully configured. If you need to set it up on a new machine, see [DATABASE_SETUP.md](DATABASE_SETUP.md) for detailed instructions.

**Quick setup** (for new installations):
```bash
# Create database
sudo -u postgres createdb lishebora

# Set password for postgres user (if not already set)
sudo -u postgres psql -c "ALTER USER postgres PASSWORD 'postgres';"

# Run migrations
alembic upgrade head
```

**Current status**: All tables are created and migrations are up-to-date.

6. **Run the development server**

```bash
uvicorn app.main:app --reload
```

7. **Open the demo web page**

Visit `http://localhost:8000` in your browser to upload a sample image and see the structured response.

**Note**: All extracted data is automatically saved to the database for caching, analytics, and research purposes. The database is fully configured with all tables created via Alembic migrations.

#### Access from your phone (same Wi‑Fi)

To use the demo on your phone (e.g. to test camera capture):

1. **Run the server so it accepts external connections**
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

2. **Confirm the correct IP**
   - On **Windows** (Command Prompt or PowerShell): run `ipconfig` and note the **IPv4 Address** under your active adapter (e.g. Wi‑Fi or Ethernet). Example: `192.168.150.245`.
   - That Windows IP is what your phone must use. WSL’s own IP (e.g. `172.x.x.x`) is only visible inside your PC.

3. **If you run the app inside WSL2**: Windows does not forward port 8000 to WSL by default, so `http://192.168.150.245:8000` from your phone may not work until you add a port proxy on **Windows** (run PowerShell **as Administrator**):
   ```powershell
   # Get WSL’s IP (run in WSL): hostname -I
   # Then on Windows (replace 172.20.0.1 with your WSL IPv4 from hostname -I):
   netsh interface portproxy add v4tov4 listenport=8000 listenaddress=0.0.0.0 connectport=8000 connectaddress=172.20.0.1
   ```
   If your WSL IP changes after reboot, run the same command again with the new IP, or remove the rule first:  
   `netsh interface portproxy delete v4tov4 listenport=8000 listenaddress=0.0.0.0`

4. **Open on your phone** (same Wi‑Fi):  
   `http://192.168.150.245:8000`  
   (Use the IPv4 from step 2; 192.168.150.245 is only an example.)

If it still doesn’t load, check Windows Firewall: allow inbound TCP on port 8000 for the app or for “Private” networks.

**Check IP and port from the terminal**

- **Windows (PowerShell)** – see which IP the PC has and whether port 8000 is listening:
  ```powershell
  ipconfig | findstr /i "IPv4"
  netstat -an | findstr "8000"
  ```
  Use the IPv4 address shown (e.g. `192.168.150.245`) as the URL on your phone: `http://<that-IP>:8000`. If the only line for 8000 is `127.0.0.1:8000 ... LISTENING`, the app is only accepting local connections; run uvicorn with `--host 0.0.0.0` (and set up the WSL port proxy if you use WSL2).
- **WSL/Linux** – see the machine’s IP and whether something listens on 8000:
  ```bash
  hostname -I
  ss -tlnp | grep 8000
  ```

---

## 🐳 Docker Setup

For a containerized setup with Docker and Docker Compose:

### Prerequisites

- Docker (version 20.10+)
- Docker Compose (version 2.0+)

### Quick Start with Docker

1. **Set up environment variables**

   Create a `.env` file:
   ```bash
   OPENAI_API_KEY=sk-...
   DATABASE_URL=postgresql://postgres:postgres@db:5432/lishebora
   ```
   (Match `DATABASE_URL` to your `docker-compose.yml` Postgres service.)

2. **Start services**

   ```bash
   docker-compose up -d
   ```

3. **Run database migrations**

   ```bash
   docker-compose exec api alembic upgrade head
   ```

4. **Access the application**

   - API: http://localhost:8000
   - API Docs: http://localhost:8000/docs

### Common Docker Commands

```bash
# View logs
docker-compose logs -f

# Stop services
docker-compose stop

# Restart services
docker-compose restart

# Rebuild after code changes
docker-compose up -d --build
```

For detailed Docker setup instructions, see [DOCKER_SETUP.md](DOCKER_SETUP.md).

### AWS deployment

AWS-related files (deploy scripts, credentials helpers, and deployment docs) live in the **`aws/`** folder. The **`aws/`** folder is in **`.gitignore`** so credentials and keys are never committed. See `aws/README.md` in that folder for deployment steps (e.g. deploy to an existing EC2 instance).

---

## API Documentation

### Base URL

```
http://localhost:8000
```

### Endpoints

#### `GET /`

**Description**: Simple HTML demo page with image upload form

**Response**: HTML page with upload form and result display

---

#### `POST /extract`

**Description**: Extract ingredients from a food label image

**Request**:
- **Method**: `POST`
- **Content-Type**: `multipart/form-data`
- **Body**: 
  - `image` (file): Image file (JPEG, PNG, etc.)

**Response**:
```json
{
  "ingredients": [
    {
      "name": "Potato"
    },
    {
      "name": "Refined Palmolein Oil"
    },
    ...
  ],
  "nutrition_per_100g": {
    "energy_kcal": 520,
    "total_fat": 30.0,
    "saturated_fat": 12.0,
    "trans_fat": 0.0,
    "total_sugar": 2.5,
    "sodium": 0.5,
    "protein": 5.0,
    "carbohydrates": 55.0,
    "fiber": 3.0,
    "additional_nutrients": {
      "potassium": 200,
      "calcium": 50,
      "iron": 2.5
    }
  },
  "product_info": {
    "name": "Potato Chips",
    "brand": "Brand X",
    "category": "snacks",
    "barcode": "1234567890123"
  },
  "raw_text": "INGREDIENTS: Potato, Refined Palmolein Oil, ...",
  "extraction_metadata": {
    "ingredients_found": true,
    "nutrition_facts_found": true,
    "product_name_found": true,
    "barcode_found": true
  },
  "warnings": [],
  "errors": [],
  "knpm_label": {
    "classification": "LESS_HEALTHY",
    "octagons": ["HIGH_IN_SUGAR"],
    "reasons": ["Total sugar … g/100g exceeds KNPM threshold …"],
    "message": null
  },
  "model_raw_output": {
    "output": "..."
  }
}
```

When nutrition cannot be evaluated, `classification` may be `"UNKNOWN"` and `message` explains why (e.g. no numeric nutrition on the label).

**Response Model**: `OcrResult`

**Status Codes**:
- `200 OK`: Success
- `400 Bad Request`: Invalid file type or missing image
- `500 Internal Server Error`: OCR processing failed

**Example (cURL)**:
```bash
curl -X POST "http://localhost:8000/extract" \
  -F "image=@path/to/label.jpg"
```

**Example (Python)**:
```python
import requests

with open("label.jpg", "rb") as f:
    response = requests.post(
        "http://localhost:8000/extract",
        files={"image": f}
    )
    result = response.json()
    print(result["ingredients"])
```

---

#### `GET /health`

**Description**: Health check endpoint

**Response**:
```json
{
  "status": "ok"
}
```

---

### Interactive API Docs

FastAPI automatically generates interactive API documentation:

- **Swagger UI**: `http://localhost:8000/docs`
- **ReDoc**: `http://localhost:8000/redoc`

---

## How It Works

### OCR Pipeline

1. **Image Upload**: User uploads an image via the `/extract` endpoint
2. **Image Validation**: FastAPI validates the file is an image
3. **OCR Client**: `extract_ingredients_from_image()` processes the image:
   - Encodes the image for OpenAI vision (base64 data URL)
   - Calls **OpenAI** chat completions with a structured JSON prompt (default `gpt-4.1-mini`)
4. **Response Processing**:
   - Cleans the model response (removes markdown fences, extracts JSON)
   - Parses JSON to extract:
     - Ingredient list
     - **All nutrition values** (core KNPM nutrients + any additional nutrients visible)
     - Product information (name, brand, category, barcode)
     - Extraction metadata (what was found/missing)
   - Validates nutrition values (non-negative, reasonable ranges)
   - Detects trans fats and artificial sweeteners from ingredients
   - Runs **`knpm_labeller.classify_with_knpm()`** to set `knpm_label` (and may append a short message to `warnings`)
   - Runs **`supermarket_lookup.lookup_supermarket_classification()`** to set `supermarket_classification` from the POS lookup CSV (exact, then fuzzy)
   - Generates warnings/errors for missing data
5. **Persistence**: `save_ocr_result_to_db()` stores the scan and related rows when the DB is configured (merges `supermarket_classification` into `model_raw_output` on the scan)
6. **Structured Output**: Returns `OcrResult` including `knpm_label`, `supermarket_classification`, ingredients, nutrition, product info, and metadata

### Prompt Engineering

The system uses a carefully crafted prompt to ensure consistent JSON output:

- **System Prompt**: Instructs the model to extract:
  - Ingredients list
  - **All nutrients** visible in the nutrition facts table (not just predefined ones)
  - Product information (name, brand, category, barcode)
  - Extraction metadata
- **User Prompt**: Asks for complete extraction with specific JSON structure
- **Temperature**: Set to 0.1 for consistent, deterministic output
- **Max Tokens**: 1024 tokens (increased to handle full nutrition facts tables)
- **Key Feature**: Extracts **all nutrients** found on the label, storing additional ones (potassium, calcium, iron, vitamins, etc.) in `additional_nutrients` dict

### Response Cleaning

The `_clean_response_text()` function handles common model quirks:

- Removes markdown code fences (```json ... ```)
- Extracts JSON object boundaries
- Handles trailing commentary or extra text

### JSON Parsing

The parsing functions handle multiple data types:

- **`_parse_ingredients_from_model_text()`**: Extracts ingredient list
- **`_parse_nutrition_data()`**: Extracts and validates:
  - Core KNPM nutrients (explicit fields for easy access)
  - **Additional nutrients** (potassium, calcium, iron, vitamins, etc.) stored in `additional_nutrients` dict
  - Validates all values (non-negative, reasonable ranges)
- **`_parse_product_info()`**: Extracts product name, brand, category, barcode
- **`_detect_trans_fats_and_sweeteners()`**: Scans ingredients for trans fat and artificial sweetener keywords
- All functions use strict error handling (return empty/null on parse failure)

---

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `OPENAI_API_KEY` | **Yes** (for `/extract`) | - | OpenAI API key; used for vision OCR |
| `OPENAI_MODEL` | No | `gpt-4.1-mini` | Chat/vision model name |
| `DATABASE_URL` | No | `postgresql://postgres@localhost:5432/lishebora` | PostgreSQL connection URL |
| `REPLICATE_API_TOKEN` | No | - | Legacy; not used by default OCR path |
| `REPLICATE_MODEL` | No | `openai/gpt-4.1-mini` | Legacy Replicate model id |

### Configuration File

Settings are managed in `app/config.py`:

- Loads environment variables from `.env` file
- Provides type-safe settings via Pydantic-like class
- Cached singleton pattern for performance

---

## Project Structure

```
lishebora_vic/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, demo HTML, static /octagon_images
│   ├── config.py
│   ├── db.py
│   ├── models.py            # Pydantic models (OcrResult, KnpmLabel, SupermarketClassification, …)
│   ├── database/
│   │   └── models.py        # SQLAlchemy tables
│   └── services/
│       ├── ocr_client.py         # OpenAI vision + parsing + KNPM + POS lookup
│       ├── knpm_labeller.py      # KNPM v1 classification
│       ├── supermarket_lookup.py # POS class/subclass/NOVA from lookup CSV
│       └── db_service.py         # Persist scans / products / nutrition
├── alembic/                 # Migrations
├── octagon_images/          # SVG assets for demo (high sugar/salt/fat)
├── ingredient_image_data/   # Sample test images (tracked in git)
├── data/
│   ├── huge_data.csv                    # Wide POS export (many columns)
│   ├── product_class_subclass_lookup.csv # Slim lookup: description → class/subclass/nova
│   └── all_categories_combined.csv      # Other nutrition/category reference
├── notebooks/               # Jupyter notebooks (experimentation)
├── docker-compose.yml
├── Dockerfile
├── .env                     # Not in git
├── .gitignore
├── requirements.txt
└── README.md
```

### Key Files

- **`app/main.py`**: Routes `/`, `/extract`, `/health`; demo UI; mounts `octagon_images`
- **`app/services/ocr_client.py`**: OpenAI vision OCR and JSON parsing
- **`app/services/knpm_labeller.py`**: KNPM-style `knpm_label` generation
- **`app/models.py`**: `OcrResult`, `KnpmLabel`, nutrition and product models
- **`app/config.py`**: `OPENAI_*`, `DATABASE_URL`, `SUPERMARKET_*` lookup paths/scores, legacy Replicate vars
- **`app/services/supermarket_lookup.py`**: Load lookup CSV; exact + fuzzy match to `supermarket_classification`

### Supermarket product class / subclass (data pipeline)

For KNPM, product **category** often depends on **class** and **subclass** from your supermarket taxonomy (`class_name`, `subclass_name` in `huge_data.csv`).

| File | Purpose |
|------|--------|
| `data/huge_data.csv` | Full POS-style export (transactions, pricing, many columns). |
| **`data/product_class_subclass_lookup.csv`** | **Trimmed reference**: `description`, `class_name`, `subclass_name`, `nova`. Built by stripping **pack sizes** (e.g. 500ML, 1L, 1KG, **200G/**, **35G/**), **piece counts** (**300PCS**, **6PCS**, `(12PCS)`, **65X5PCS**, **4PK**/**8PK**), **/KG**, counts like **10S**/**80S**/**6S**/**14S/**, trailing `5*`, **4/6/8 J/SUPER**, bare **J/SUPER**, empty `( )`, `/ (6S)` after cubes, slash-with-spaces-on-both-sides (without touching **S/BERRY**, **T/BAG**, **G/TOP**), multipacks (**6X300ML**), patterns like `400* V/` and `5*1.6G/`, trailing small **pack counts**, **commas**, **full stops** (including trailing and abbreviation dots like `G.` / `ORIG.` / `BISC.`, but not decimal points in numbers), standalone **`PL`**, **EOT** / **`E O T`**, expansions **`CHOC`→`CHOCOLATE`**, **`BISC`→`BISCUIT`**, removal of **`CT`**, **`M/B`**, **`M/BAK`**, **`M/BAKERS`**, and common pack tokens (PET, BTL, TR, GLASS, CTN, POUCH, …), then **deduplicating** — same logical product in different sizes becomes **one row** (aligned with nutrition per 100g/ml). |
| `scripts/build_product_classification_lookup.py` | Regenerates the lookup from `huge_data.csv`. Default: strip pack + dedupe. Use `--no-strip-pack` for legacy exact-POS lines only. |
| `app/utils/pos_description.py` | Shared `normalize_pack_description()` used by the build script and **runtime** `supermarket_lookup` so OCR names with sizes still match. |

**Regenerate lookup**

```bash
python scripts/build_product_classification_lookup.py
```

**Choosing a format (CSV vs alternatives)**

- **CSV** — Fine for lookups up to hundreds of thousands of unique descriptions: load once at startup into a `dict` keyed by normalized description, or use **pandas** for fuzzy joins. Easy to edit and diff in Git.
- **SQLite** — Good when the table grows large or you want SQL (indexes, `LIKE`, joins) without running Postgres migrations for reference data only.
- **PostgreSQL seed table** — Best if the same lookup must be shared across app instances and updated operationally like other app data.

**Pipeline (implemented)** — After OCR, classification uses `data/product_class_subclass_lookup.csv` in two steps: (1) **Product line** — `product_info.name` (and `brand` + `name`) vs column `description`, after the **same pack-size normalization** as the CSV build (then exact match, then fuzzy **max(WRatio, partial_ratio)**). Cutoff: `SUPERMARKET_FUZZY_MIN_SCORE` (default `72`). (2) **Taxonomy fallback** — if no line hit, `product_info.category` vs distinct POS `subclass_name` / `class_name` (`token_set_ratio`; cutoff `SUPERMARKET_TAXONOMY_FUZZY_MIN_SCORE`, default `52`). Supports **healthy alternatives** (same subclass → suggest fitter SKUs). Results: `OcrResult.supermarket_classification` and top-level `class_name` / `subclass_name`; merged into `Scan.model_raw_output`. Override CSV path with `SUPERMARKET_LOOKUP_CSV`.

**POS vs label (important)** — Retail taxonomy is **not** a nutrition claim. It can disagree with the pack (e.g. SKU matched to “no added sugar” in POS while the label shows high sugar). **KNPM uses the nutrition table**; when POS wording implies no/low added sugar but `knpm_label` includes `HIGH_IN_SUGAR`, the API adds a **`warnings`** entry via `classification_consistency.warning_pos_taxonomy_vs_label_sugar` so clients can explain the mismatch and avoid recommending alternatives purely from the conflicting subclass.

---

## Testing

### Manual Testing

1. **Using the Web Interface**:
   - Visit `http://localhost:8000`
   - Upload an image from `ingredient_image_data/` folder
   - Check the structured output

2. **Using cURL**:
```bash
curl -X POST "http://localhost:8000/extract" \
  -F "image=@ingredient_image_data/sample1.jpg"
```

3. **Using Python**:
```python
import requests

with open("ingredient_image_data/sample1.jpg", "rb") as f:
    response = requests.post(
        "http://localhost:8000/extract",
        files={"image": f}
    )
    print(response.json())
```

### Test Images

The `ingredient_image_data/` folder contains sample food label images for testing. These images are used to validate the OCR pipeline's accuracy and robustness.

### Expected Output Format

A successful response should contain:

- **`ingredients`**: Array of ingredient objects with `name` field
- **`nutrition_per_100g`**: Complete nutrition data with:
  - Core KNPM nutrients (explicit fields): energy, fats, sugar, sodium, protein, carbs, fiber
  - **`additional_nutrients`**: Dict with all other nutrients found (potassium, calcium, iron, vitamins, etc.)
- **`product_info`**: Product name, brand, category, barcode (if available)
- **`class_name`** / **`subclass_name`**: Supermarket POS taxonomy for this scan (top-level; `null` if the product could not be resolved against the lookup CSV). Same values as inside `supermarket_classification` when present.
- **`extraction_metadata`**: Flags indicating what was found/missing
- **`warnings`**: Array of warnings about missing or incomplete data
- **`errors`**: Array of errors that prevent further processing
- **`raw_text`**: Full text extracted from the label (if available)
- **`model_raw_output`**: Raw model response for debugging
- **`knpm_label`**: Classification, octagon codes, reasons, optional `message` when `UNKNOWN`
- **`supermarket_classification`**: When the OCR product name matches `data/product_class_subclass_lookup.csv`, includes `class_name`, `subclass_name`, `nova`, `matched_description`, `match_method`, and `match_score` (fuzzy only); otherwise `null`

Example:
```json
{
  "ingredients": [
    {"name": "Potato"},
    {"name": "Refined Palmolein Oil"},
    {"name": "Bengal Gram Flour"}
  ],
  "nutrition_per_100g": {
    "energy_kcal": 520,
    "total_fat": 30.0,
    "saturated_fat": 12.0,
    "trans_fat": 0.0,
    "total_sugar": 2.5,
    "sodium": 0.5,
    "protein": 5.0,
    "carbohydrates": 55.0,
    "fiber": 3.0,
    "additional_nutrients": {
      "potassium": 200,
      "calcium": 50,
      "iron": 2.5
    }
  },
  "product_info": {
    "name": "Potato Chips",
    "brand": "Brand X",
    "category": "snacks",
    "barcode": "1234567890123"
  },
  "extraction_metadata": {
    "ingredients_found": true,
    "nutrition_facts_found": true,
    "product_name_found": true,
    "barcode_found": true
  },
  "warnings": [],
  "errors": [],
  "raw_text": "INGREDIENTS: Potato, Refined Palmolein Oil, ...",
  "knpm_label": {
    "classification": "FIT_FOR_CONSUMPTION",
    "octagons": [],
    "reasons": ["All nutrients of concern are within KNPM thresholds."],
    "message": null
  },
  "model_raw_output": {"output": "..."}
}
```

---

## Next Steps

### Immediate (Phase 1 Completion)

- [x] Extract ingredients from food labels
- [x] Extract complete nutrition facts (all nutrients visible on label)
- [x] Extract product information (name, brand, category, barcode)
- [x] Handle missing data gracefully with warnings/errors
- [ ] Test with more diverse images (different languages, lighting conditions, label formats)
- [ ] Add validation against known ingredient databases
- [ ] Improve error messages for edge cases

### Phase 2: KNPM Labeling

- [x] First-pass labeling algorithm (sugar / fat / sat fat / sodium + ingredient flags) → `knpm_label` on `/extract`
- [ ] Official category-specific KNPM thresholds and product-type rules
- [ ] Configurable thresholds (env or DB) instead of hardcoded demo values
- [ ] Optional dedicated `/label` endpoint (reuse labeller on stored nutrition)

### Phase 3: Nutrition Validation

- [ ] Integrate Open Food Facts API
- [ ] Integrate Kenya Food Composition Tables
- [ ] Validate extracted label data against reference databases

### Phase 4: Recommendations & Explanations

- [ ] Build recommendation engine (healthier alternatives)
- [ ] Integrate GenAI for explanations
- [ ] Add personalization based on user preferences

### Phase 5: Database & Production

- [x] Set up PostgreSQL database
- [x] Create database schema and migrations (Alembic)
- [x] Implement product caching (automatic via database)
- [x] Database storage for all extracted data
- [ ] Add user authentication
- [ ] Add analytics endpoints (query scan data)
- [ ] Add product search API
- [ ] Add analytics and logging
- [ ] Deploy to production environment

---

## Development Notes

### Code Style

- Follows PEP 8 Python style guide
- Uses type hints throughout
- Pydantic models for data validation
- Async/await for I/O operations

### Error Handling

- Custom `OcrClientError` exception for OCR-specific errors
- HTTP exceptions for API-level errors
- Defensive parsing with fallbacks

### Performance Considerations

- OpenAI client calls run in a worker thread (`anyio.to_thread`) so the async event loop is not blocked
- Response cleaning is lightweight (string operations)
- JSON parsing is strict (fails fast on invalid data)

---

## Contributing

This is a research project. For contributions or questions, please contact the project team.

---

## License

[To be determined by APHRC]

---

## Acknowledgments

- **APHRC** (African Population and Health Research Center) for project leadership
- **Ministry of Health, Kenya** for KNPM framework
- **OpenAI** for vision-capable GPT models used in OCR
- **Open Food Facts** for open food database

---

## Contact

For questions or issues, please contact the development team.

---

**Last Updated**: January 2026  
**Version**: 0.5.0 — OpenAI OCR, KNPM labeller v1, demo octagon UI, PostgreSQL persistence, Docker; see [CHANGELOG.md](CHANGELOG.md).