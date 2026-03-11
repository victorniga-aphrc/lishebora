from fastapi import Depends, FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import OcrResult
from app.services.db_service import save_ocr_result_to_db
from app.services.ocr_client import OcrClientError, extract_ingredients_from_image


app = FastAPI(title="Lishebora VIC Backend")


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """Simple demo page with an image upload form."""
    return """
    <!doctype html>
    <html lang="en">
      <head>
        <meta charset="utf-8">
        <title>Lishebora OCR Demo</title>
        <style>
          body { font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 2rem; }
          .container { max-width: 820px; margin: 0 auto; }
          .card { border: 1px solid #e5e7eb; border-radius: 0.75rem; padding: 1.5rem; box-shadow: 0 10px 15px -3px rgba(15,23,42,0.08); }
          h1 { font-size: 1.75rem; margin-bottom: 0.5rem; }
          p { color: #4b5563; margin-bottom: 1rem; }
          .input-group { margin: 1rem 0; display: flex; flex-direction: column; gap: 0.5rem; }
          label { font-weight: 500; color: #111827; }
          input[type="file"] { padding: 0.25rem 0; }
          button { background-color: #2563eb; color: white; border: none; padding: 0.5rem 1rem; border-radius: 0.5rem; cursor: pointer; }
          button:hover { background-color: #1d4ed8; }
          pre { background: #0f172a; color: #e5e7eb; padding: 1rem; border-radius: 0.5rem; overflow-x: auto; }
        </style>
      </head>
      <body>
        <div class="container">
          <div class="card">
            <h1>Lishebora OCR Demo</h1>
            <p>Upload a food label image (from file) or capture one using your device camera to extract a structured list of ingredients and nutrition data.</p>
            <form id="upload-form">
              <div class="input-group">
                <label for="file-input">Option 1: Upload from files</label>
                <input id="file-input" type="file" name="file_image" accept="image/*" />
              </div>
              <div class="input-group">
                <label for="camera-input">Option 2: Capture from camera (mobile devices)</label>
                <input
                  id="camera-input"
                  type="file"
                  name="camera_image"
                  accept="image/*"
                  capture="environment"
                />
              </div>
              <button type="submit">Upload and Extract</button>
            </form>
            <h2>Structured Output</h2>
            <pre id="result">Waiting for upload...</pre>
          </div>
        </div>
        <script>
          const form = document.getElementById("upload-form");
          const resultEl = document.getElementById("result");
          const fileInput = document.getElementById("file-input");
          const cameraInput = document.getElementById("camera-input");

          form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const formData = new FormData();

            // Prefer camera image if provided, otherwise fall back to file upload
            const cameraFile = cameraInput.files[0];
            const file = fileInput.files[0];

            if (cameraFile) {
              formData.append("image", cameraFile);
            } else if (file) {
              formData.append("image", file);
            } else {
              resultEl.textContent = "Please select or capture an image first.";
              return;
            }

            resultEl.textContent = "Processing...";
            try {
              const response = await fetch("/extract", {
                method: "POST",
                body: formData
              });
              const data = await response.json();
              resultEl.textContent = JSON.stringify(data, null, 2);
            } catch (err) {
              resultEl.textContent = "Error: " + err;
            }
          });
        </script>
      </body>
    </html>
    """


@app.post("/extract", response_model=OcrResult)
async def extract(
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> OcrResult:
    """
    Accept an image upload, extract data, save to database, and return structured OCR result.
    
    The extracted data is automatically saved to the database for:
    - Product caching (avoid re-scanning same products)
    - Analytics and research
    - Historical tracking
    """
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image files are accepted.")

    try:
        image_bytes = await image.read()
        result = await extract_ingredients_from_image(image_bytes)
        
        # Save to database
        try:
            product, scan = save_ocr_result_to_db(
                db=db,
                ocr_result=result,
                user_id=None,  # TODO: Add authentication
                location=None,  # TODO: Extract from request if available
                image_path=None,  # TODO: Save image to storage if needed
            )
            # Optionally add database IDs to response
            # result.product_id = product.id
            # result.scan_id = scan.id
        except Exception as db_exc:
            # Log database error but don't fail the request
            # The OCR extraction was successful, so we still return the result
            print(f"Warning: Failed to save to database: {db_exc}")
        
        return result
    except OcrClientError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail="Unexpected error during OCR.") from exc


@app.get("/health", response_class=JSONResponse)
async def health() -> dict:
    """Simple health check endpoint."""
    return {"status": "ok"}

