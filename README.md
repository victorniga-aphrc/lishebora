# Lishebora backend API

Backend service for the Lishebora nutrition labelling workstream: it accepts photos of **packaged food labels**, extracts structured data with an OpenAI vision model, applies Kenya Nutrient Profile Model (KNPM) style checks, resolves nutrition and taxonomy against a PostgreSQL reference catalog when needed, and can suggest healthier substitutes when a product is classified as less healthy.

## Table of contents

- [Overview](#overview)
- [Capabilities](#capabilities)
- [Architecture](#architecture)
- [Quick start](#quick-start)
- [Docker](#docker)
- [API](#api)
- [Processing pipeline](#processing-pipeline)
- [Configuration](#configuration)
- [Project layout](#project-layout)
- [Testing](#testing)

---

## Overview

The service is intended for research and prototyping: label images go in, a structured `OcrResult` JSON document comes out. Optional PostgreSQL persistence uses **two** objects: read **`catalog.reference_products`** during the pipeline; after each successful **`POST /extract`**, append one row to **`app.product_scan_summary`** and **merge** into **`catalog.reference_products`** by exact normalized product name (new rows inserted; existing rows only get **NULL** columns filled from the scan, including KNPM **octagon count**). Summary fields include name, brand, barcode, fat/sodium/sugar per 100 g/ml, class/subclass, NOVA, KNPM octagon count, optional `user_id` / `location` / `image_path`. Run **`alembic upgrade head`** so `catalog.reference_products` has the `octagon_count` column before relying on catalog writes.

---

## Capabilities

| Area | Description |
|------|-------------|
| HTTP API | `GET /` demo page, `POST /extract` (multipart image), `POST /recommend/substitutes` (JSON body with a prior result), `GET /health`. Static KNPM octagon assets under `/octagon_images/`. |
| Vision extraction | OpenAI vision (default `gpt-4.1-mini`) returns JSON; the app parses ingredients, pipeline nutrition fields, product fields, and visual cues. Requires `OPENAI_API_KEY`. |
| Nutrition (API) | `nutrition_per_100g` exposes **`total_fat`**, **`trans_fat`**, **`total_sugar`**, **`sodium`** (g per 100 g or 100 ml). Values come from the label when usable; otherwise from the reference table when a row matches. |
| KNPM | `knpm_label` with `classification` **`healthy`**, **`less healthy`**, or **`unknown`**, plus `octagons` (e.g. `high_in_sugar`, `high_in_salt`, `high_in_fat`), `reasons`, and optional `message`. Thresholds are set via environment variables. |
| Taxonomy | `product_classification` and mirrored top-level `class_name` / `subclass_name` from `catalog.reference_products`; optional **foodclasses BiLSTM** fills weak or missing catalog matches. |
| Substitutes | When enabled and KNPM is `less healthy`, `healthier_substitutes` lists tiered alternatives from the same reference catalog (default up to **3** items), with optional template or OpenAI explanation text. |
| Persistence | Alembic + `app.product_scan_summary` plus write-through to `catalog.reference_products` on each successful `/extract` when the database is configured (`save_ocr_result_to_db`). |

---

## Architecture

**Stack:** Python 3.11+, FastAPI, Uvicorn, Pydantic, SQLAlchemy, Alembic, PostgreSQL. Optional TensorFlow for the foodclasses Keras model (see `requirements.txt`).

**Request flow (summary):**

```
Client → POST /extract → OpenAI vision → parse & validate
       → early exit if non-food or not packaged retail label
       → reference DB nutrition/taxonomy if needed → KNPM → substitutes (if applicable)
       → optional DB save → JSON response
```

**Main modules:** `app/main.py` (routes), `app/services/ocr_client.py` (orchestration), `app/services/reference_catalog_db.py` (reference lookups), `app/services/knpm_labeller.py`, `app/services/foodclasses_bilstm_inference.py`, `app/services/healthier_substitutes.py`, `app/services/recommendation_explainer.py`, `app/services/db_service.py`, `app/models.py` (schemas).

---

## Quick start

**Prerequisites:** Python 3.11+, PostgreSQL, OpenAI API key with access to the configured vision model.

1. Create and activate a virtual environment, then install dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. Copy **`.env.example`** to **`.env`** and set at least `OPENAI_API_KEY` and `DATABASE_URL`.

3. Create the database (e.g. **`lishebora`**), run **`alembic upgrade head`** (creates **`app`** / **`catalog`** and the two persistence tables), load **`catalog.reference_products`** (e.g. **`python scripts/load_simulated_reference_to_postgres.py`**), then start the API.

   ```bash
   alembic upgrade head
   python scripts/load_simulated_reference_to_postgres.py
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

4. Open `http://localhost:8000` for the demo upload page, or `http://localhost:8000/docs` for the interactive OpenAPI (Swagger) UI.

**Remote devices (same network):** bind `--host 0.0.0.0`, use the machine’s LAN IPv4 address from the client, and allow the chosen port through the host firewall. If the app runs inside WSL2, you may need a Windows port proxy from the LAN interface to the WSL IP.

---

## Docker

With Docker Compose, set `OPENAI_API_KEY` and a `DATABASE_URL` that matches the Postgres service in `docker-compose.yml`, then:

```bash
docker-compose up -d
docker-compose exec api alembic upgrade head
```

The API is available on port 8000 by default. Adjust `docker-compose.yml` and service env vars as needed for your environment.

---

## API

**Base URL:** `http://localhost:8000` (adjust host/port as needed).

### `GET /`

Serves the static demo page (image file upload and result panels).

### `POST /extract`

- **Content-Type:** `multipart/form-data`
- **Fields:** `image` (file, required); `user_goal` (optional string, passed through to substitute explanation when GenAI explanation is enabled).

**Response:** `OcrResult` JSON. Core fields include:

- `ingredients` — list of `{ "name": "..." }`
- `nutrition_per_100g` — `total_fat`, `trans_fat`, `total_sugar`, `sodium` or `null` fields
- `product_info` — `name`, `brand`, `category`, `barcode`, `visual_product_type`
- `visual_is_food`, `visual_is_packaged_retail_food`, `visual_labels`
- `extraction_metadata` — `ingredients_found`, `nutrition_facts_found`, `product_name_found`, `barcode_found`
- `warnings`, `errors`, `raw_text`, `parse_error`, `nutrition_source`, `product_nutrition_match`, `product_classification`, `foodclasses_bilstm_prediction`, `knpm_label`, `healthier_substitutes`, `model_raw_output`

Example (illustrative):

```json
{
  "ingredients": [{"name": "Whole grain wheat"}, {"name": "Sugar"}],
  "nutrition_per_100g": {
    "total_fat": 2.5,
    "trans_fat": null,
    "total_sugar": 18.0,
    "sodium": 0.12
  },
  "product_info": {
    "name": "Breakfast cereal",
    "brand": "Example Co",
    "category": null,
    "barcode": "5901234123457",
    "visual_product_type": "boxed cereal"
  },
  "knpm_label": {
    "classification": "less healthy",
    "octagons": ["high_in_sugar"],
    "reasons": ["Total sugar … exceeds KNPM limit …"],
    "message": null
  },
  "nutrition_source": "image",
  "errors": [],
  "warnings": []
}
```

### `POST /recommend/substitutes`

**Content-Type:** `application/json`

Body: `{ "ocr_result": { ... full OcrResult ... }, "user_goal": "optional string" }`

Returns a `HealthierSubstituteResult` in the same shape as `OcrResult.healthier_substitutes`.

### `GET /health`

Returns `{"status": "ok"}` when the process is up.

---

## Processing pipeline

1. The image is sent to the vision model; the response is cleaned and parsed as JSON.
2. If `visual_is_food` is false, the API returns early with an error and does not run catalog classification or KNPM on a full pipeline result.
3. If `visual_is_packaged_retail_food` is false (e.g. loose or unpackaged food), the API returns early with an error: the tool targets packaged retail labels.
4. If the label does not yield usable numeric nutrition for the four pipeline nutrients, the service looks up the product name in **`catalog.reference_products`** (exact match, then fuzzy), and sets `nutrition_source` and `product_nutrition_match` when successful.
5. Taxonomy is resolved from the same table; if the catalog match is weak or missing and the foodclasses model is enabled, the BiLSTM prediction is merged according to configured rules. NOVA strings are normalized using `models/nova_labels.json` when applicable.
6. KNPM runs on resolved nutrition plus ingredient-keyword gates (trans fat wording, non-nutritive sweeteners).
7. If substitutes are enabled and classification is `less healthy`, candidates are drawn from the reference catalog (zero KNPM octagons only), tiered by subclass then class then full catalog, up to the configured min/max count; explanation text may be template- or OpenAI-generated.
8. The result is returned; the route handler may persist to PostgreSQL without failing the HTTP response if save fails.

---

## Configuration

Settings are defined in **`app/config.py`** and loaded from the process environment. A **`.env`** file in the project root is picked up automatically (`python-dotenv`). Copy **`.env.example`** to `.env` and adjust values there.

**`OPENAI_API_KEY`** is required for **`POST /extract`**. Everything else has a default in code.

| Variable | Default | Role |
|----------|---------|------|
| `OPENAI_MODEL` | `gpt-4.1-mini` | Vision-capable chat model id. |
| `DATABASE_URL` | `postgresql://postgres@localhost:5432/lishebora` | PostgreSQL for persistence and reference lookups. |
| `REFERENCE_CATALOG_FUZZY_MIN_SCORE` | `90` | Minimum fuzzy name score (0–100) to accept a catalog row for nutrition and taxonomy when there is no exact normalized name match; lower only if recall is too strict. |
| `KNPM_FAT_THRESHOLD` | `7.76` | Total fat limit (g per 100 g/ml) for “high in fat”. |
| `KNPM_SUGAR_THRESHOLD` | `4.7` | Total sugar limit (g per 100 g/ml). |
| `KNPM_SODIUM_THRESHOLD` | `0.26` | Sodium limit (g per 100 g/ml). |
| `FOODCLASSES_BILSTM_ENABLED` | `true` | If `true`, run the foodclasses model when the reference-catalog name match is weak. |
| `FOODCLASSES_BILSTM_MODEL_PKL` | `models/foodclasses_model.pkl` | Keras model path. |
| `FOODCLASSES_BILSTM_TOKENIZER_PKL` | `models/tokenizer.pkl` | Tokenizer path. |
| `FOODCLASSES_BILSTM_LABEL_ENCODERS_PKL` | `models/label_encoders.pkl` | Label encoders (match **scikit-learn** version in `requirements.txt`). |
| `FOODCLASSES_BILSTM_REFERENCE_WEAK_MAX_SCORE` | `70` | Reference fuzzy name score **below** this ⇒ weak match (model may run). Exact name match is always strong. |
| `NOVA_LABELS_JSON` | `models/nova_labels.json` | JSON map for normalized NOVA strings on the API. |
| `SUBSTITUTE_RECOMMENDATIONS_ENABLED` | `true` | Build `healthier_substitutes` when KNPM classification is `less healthy`. |
| `SUBSTITUTE_MIN_RESULTS` | `3` | Minimum substitutes before widening tiers. |
| `SUBSTITUTE_MAX_RESULTS` | `3` | Maximum substitutes returned. |
| `SUBSTITUTE_EXPLANATION_ENABLED` | `true` | If `true` and `OPENAI_API_KEY` is set, substitute text may use OpenAI; otherwise a template is used. |

For boolean flags, values such as `true`, `false`, `1`, `0`, `yes`, and `on` are accepted (see `app/config.py`).

---

## Project layout

```
lishebora_vic/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── db.py
│   ├── database/
│   └── services/
├── alembic/
├── static/              # Demo UI assets
├── octagon_images/      # KNPM SVGs for the demo
├── models/              # BiLSTM artifacts (pkl, nova_labels.json)
├── data/                # CSV inputs for offline loaders (not read by the API at runtime)
├── scripts/             # Database loaders and maintenance
├── local/               # Gitignored: scratch scripts, archived modules, optional tests/
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example        # Template; copy to .env (not committed)
├── LICENSE             # MIT
└── README.md
```

---

## Testing

Manual checks: upload a label image via the demo page or:

```bash
curl -X POST "http://localhost:8000/extract" -F "image=@path/to/label.jpg"
```

Automated tests are kept under **`local/tests/`** (gitignored, not part of the shipped API). To run them from the repository root: `pytest local/tests/`.

---

## Contributing and support

This repository supports an APHRC-led research effort. For contributions or questions, contact the project team.

---

## License

This project is released under the [MIT License](LICENSE).

---

## Acknowledgments

African Population and Health Research Center (APHRC); Kenya Ministry of Health KNPM framework; OpenAI vision APIs used for extraction.
