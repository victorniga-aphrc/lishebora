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

### ✅ Completed (Phase 1 - Ingredient & Nutrition Extraction)

- **Image Upload API**: Accepts images via file upload or camera capture
- **OCR Pipeline**: Uses GPT-4.1-mini (via Replicate) to extract ingredients and nutrition facts from food label images
- **Structured Output**: Returns clean JSON with:
  - List of ingredient names (parsed and cleaned)
  - **Complete nutrition facts** (all nutrients visible on label):
    - Core KNPM nutrients: energy, fats, sugar, sodium, protein, carbs, fiber
    - **Additional nutrients**: potassium, calcium, iron, vitamins, etc. (all nutrients found on label)
  - Product information (name, brand, category, barcode if visible)
  - Raw text extracted from the label
  - Extraction metadata (what was found/missing)
  - Warnings and errors for missing data
  - Model raw output (for debugging)
- **Web Demo Interface**: Simple HTML page for testing uploads and viewing results
- **Error Handling**: Robust error handling with graceful degradation and clear error messages

### 🚧 In Progress / Planned

- **KNPM Labeling Engine**: Classify products based on Kenya Nutrient Profile Model thresholds
- **Nutrition Validation**: Integration with Open Food Facts and Kenya Food Composition Tables
- **Recommendation Engine**: Suggest healthier alternatives
- **GenAI Explanations**: Generate user-friendly explanations for labeling decisions
- **Database Integration**: ✅ PostgreSQL fully implemented and configured - stores products, scans, ingredients, and nutrition data with automatic migrations
- **Authentication**: User accounts and profiles

---

## Architecture

### Tech Stack

- **Framework**: FastAPI (Python 3.11+)
- **Database**: PostgreSQL with SQLAlchemy ORM
- **Migrations**: Alembic
- **OCR/ML**: GPT-4.1-mini via Replicate API
- **Async Runtime**: Uvicorn with async/await
- **Data Models**: Pydantic for request/response validation, SQLAlchemy for database models
- **Configuration**: python-dotenv for environment variables

### System Flow

```
User Uploads Image
    ↓
FastAPI Endpoint (/extract)
    ↓
OCR Client Service
    ↓
Replicate API (GPT-4.1-mini)
    ↓
Response Cleaning & JSON Parsing
    ↓
Structured Ingredient List
    ↓
JSON Response to Client
```

### Key Components

1. **`app/main.py`**: FastAPI application with routes
2. **`app/services/ocr_client.py`**: Core OCR logic using Replicate
3. **`app/services/db_service.py`**: Database service for saving extracted data
4. **`app/models.py`**: Pydantic models for request/response validation
5. **`app/database/models.py`**: SQLAlchemy models for database tables
6. **`app/db.py`**: Database connection and session management
7. **`app/config.py`**: Configuration management

---

## Quick Start

### Prerequisites

- Python 3.11 or higher
- PostgreSQL database (see [DATABASE_SETUP.md](DATABASE_SETUP.md) for setup instructions)
- Replicate API token ([Get one here](https://replicate.com/account/api-tokens))
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
REPLICATE_API_TOKEN=your_replicate_token_here
# Optional: override default model
# REPLICATE_MODEL=openai/gpt-4.1-mini

# Database configuration
DATABASE_URL=postgresql://postgres@localhost:5432/lishebora
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
   REPLICATE_API_TOKEN=your_replicate_token_here
   ```

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
  "model_raw_output": {
    "output": "..."
  }
}
```

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
   - Converts image bytes to a file-like object
   - Prepares a structured prompt for GPT-4.1-mini
   - Calls Replicate API with the image and prompt
4. **Response Processing**:
   - Cleans the model response (removes markdown fences, extracts JSON)
   - Parses JSON to extract:
     - Ingredient list
     - **All nutrition values** (core KNPM nutrients + any additional nutrients visible)
     - Product information (name, brand, category, barcode)
     - Extraction metadata (what was found/missing)
   - Validates nutrition values (non-negative, reasonable ranges)
   - Detects trans fats and artificial sweeteners from ingredients
   - Generates warnings/errors for missing data
5. **Structured Output**: Returns `OcrResult` with ingredients, complete nutrition data, product info, and metadata

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
| `REPLICATE_API_TOKEN` | Yes | - | Replicate API token for accessing GPT-4.1-mini |
| `REPLICATE_MODEL` | No | `openai/gpt-4.1-mini` | Model identifier for Replicate |
| `DATABASE_URL` | No | `postgresql://postgres@localhost:5432/lishebora` | PostgreSQL database connection URL |

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
│   ├── main.py              # FastAPI application and routes
│   ├── config.py            # Configuration management
│   ├── models.py            # Pydantic data models
│   └── services/
│       ├── __init__.py
│       └── ocr_client.py    # OCR extraction logic
├── ingredient_image_data/   # Sample test images
├── notebooks/              # Jupyter notebooks (experimentation)
├── .env                    # Environment variables (not in git)
├── .gitignore              # Git ignore rules
├── requirements.txt        # Python dependencies
└── README.md               # This file
```

### Key Files

- **`app/main.py`**: FastAPI app with `/`, `/extract`, and `/health` endpoints
- **`app/services/ocr_client.py`**: Core OCR logic using Replicate API
- **`app/models.py`**: `Ingredient` and `OcrResult` Pydantic models
- **`app/config.py`**: Settings loader from environment variables

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
- **`extraction_metadata`**: Flags indicating what was found/missing
- **`warnings`**: Array of warnings about missing or incomplete data
- **`errors`**: Array of errors that prevent further processing
- **`raw_text`**: Full text extracted from the label (if available)
- **`model_raw_output`**: Raw model response for debugging

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

- [ ] Implement KNPM threshold configuration
- [ ] Build labeling algorithm (Fit for Consumption vs Less Healthy)
- [ ] Add product category detection
- [ ] Create `/label` endpoint that takes ingredients and returns label classification

### Phase 3: Nutrition Validation

- [ ] Integrate Open Food Facts API
- [ ] Integrate Kenya Food Composition Tables
- [ ] Add nutrition data extraction from labels
- [ ] Validate extracted data against databases

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

- Replicate API calls run in a thread pool to avoid blocking the event loop
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
- **Replicate** for hosting GPT-4.1-mini model
- **Open Food Facts** for open food database

---

## Contact

For questions or issues, please contact the development team.

---

**Last Updated**: January 2026  
**Version**: 0.3.0 (Phase 1 - Ingredient & Complete Nutrition Extraction + Database Integration)