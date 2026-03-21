from fastapi import Depends, FastAPI, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import OcrResult
from app.services.db_service import save_ocr_result_to_db
from app.services.ocr_client import OcrClientError, extract_ingredients_from_image


app = FastAPI(title="Lishebora VIC Backend")


# Serve black octagon SVGs under /octagon_images
app.mount(
    "/octagon_images",
    StaticFiles(directory="octagon_images"),
    name="octagon_images",
)

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
          .container { max-width: 960px; margin: 0 auto; display: grid; grid-template-columns: 1.1fr 1fr; gap: 1.5rem; align-items: flex-start; }
          .card { border: 1px solid #e5e7eb; border-radius: 0.75rem; padding: 1.5rem; box-shadow: 0 10px 15px -3px rgba(15,23,42,0.08); }
          h1 { font-size: 1.75rem; margin-bottom: 0.5rem; }
          p { color: #4b5563; margin-bottom: 1rem; }
          .input-group { margin: 1rem 0; display: flex; flex-direction: column; gap: 0.5rem; }
          label { font-weight: 500; color: #111827; }
          input[type="file"] { padding: 0.25rem 0; }
          button { background-color: #2563eb; color: white; border: none; padding: 0.5rem 1rem; border-radius: 0.5rem; cursor: pointer; }
          button:hover { background-color: #1d4ed8; }
          pre {
            background: #0f172a;
            color: #e5e7eb;
            padding: 1rem;
            border-radius: 0.5rem;
            overflow-x: hidden;
            overflow-y: auto;
            max-height: 420px;
            white-space: pre-wrap;
            word-break: break-word;
          }
          .knpm-card { border: 1px solid #e5e7eb; border-radius: 0.75rem; padding: 1.25rem; background: #f9fafb; }
          .knpm-header { font-weight: 600; margin-bottom: 0.5rem; }
          .knpm-status { margin-bottom: 0.75rem; font-size: 0.95rem; }
          .knpm-status.fit { color: #166534; }
          .knpm-status.less { color: #b91c1c; }
          .knpm-status.unknown { color: #6b7280; }
          .octagon-list { display: flex; flex-wrap: wrap; gap: 0.75rem; margin-bottom: 0.75rem; }
          .octagon-wrapper { display: inline-flex; flex-direction: column; align-items: center; gap: 0.25rem; }
          .octagon-frame {
            width: 92px;
            height: 92px;
            display: grid;
            place-items: center;
            overflow: hidden; /* important when scaling images */
          }
          .octagon-img {
            width: 80px;
            height: 80px;
            display: block;
            object-fit: contain;
            object-position: center;
            transform-origin: center center;
          }
          .octagon-label {
            font-size: 0.7rem;
            font-weight: 600;
            text-align: center;
            color: #111827;
          }
          .knpm-reason-list { font-size: 0.85rem; color: #4b5563; padding-left: 1rem; }
          .knpm-reason-list li { margin-bottom: 0.25rem; }
          .pos-card { border: 1px solid #e5e7eb; border-radius: 0.75rem; padding: 1.25rem; background: #f0fdf4; }
          .pos-header { font-weight: 600; margin-bottom: 0.5rem; }
          .pos-meta { font-size: 0.9rem; color: #374151; line-height: 1.5; }
          .pos-meta dt { font-weight: 600; color: #111827; float: left; clear: left; width: 8.5rem; }
          .pos-meta dd { margin-left: 8.75rem; margin-bottom: 0.35rem; }
          .pos-none { font-size: 0.9rem; color: #6b7280; }
          .sidebar-col { display: flex; flex-direction: column; gap: 1rem; }
          @media (max-width: 900px) {
            .container { grid-template-columns: 1fr; }
          }
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
          <div class="sidebar-col">
            <div class="knpm-card">
              <div class="knpm-header">KNPM Label (Demo)</div>
              <div id="knpm-status" class="knpm-status unknown">No scan yet.</div>
              <div id="knpm-octagons" class="octagon-list"></div>
              <ul id="knpm-reasons" class="knpm-reason-list"></ul>
            </div>
            <div class="pos-card">
              <div class="pos-header">Supermarket taxonomy (POS)</div>
              <div id="pos-content" class="pos-none">No scan yet.</div>
            </div>
          </div>
        </div>
        <script>
          const form = document.getElementById("upload-form");
          const resultEl = document.getElementById("result");
          const fileInput = document.getElementById("file-input");
          const cameraInput = document.getElementById("camera-input");
          const knpmStatusEl = document.getElementById("knpm-status");
          const knpmOctagonsEl = document.getElementById("knpm-octagons");
          const knpmReasonsEl = document.getElementById("knpm-reasons");
          const posContentEl = document.getElementById("pos-content");

          function resetKnpmView() {
            knpmStatusEl.textContent = "No KNPM label yet.";
            knpmStatusEl.className = "knpm-status unknown";
            knpmOctagonsEl.innerHTML = "";
            knpmReasonsEl.innerHTML = "";
          }

          function resetPosView() {
            posContentEl.className = "pos-none";
            posContentEl.textContent = "No scan yet.";
          }

          function renderSupermarketClassification(sc) {
            if (!sc) {
              resetPosView();
              posContentEl.textContent = "No match in supermarket lookup (exact or fuzzy).";
              return;
            }
            posContentEl.className = "pos-meta";
            const rows = [
              ["Class", sc.class_name || "—"],
              ["Subclass", sc.subclass_name || "—"],
              ["NOVA", sc.nova || "—"],
              ["Matched description", sc.matched_description || "—"],
              ["Match method", sc.match_method || "—"],
              ["Fuzzy score", sc.match_score != null ? String(sc.match_score) : "— (exact)"],
            ];
            const dl = document.createElement("dl");
            rows.forEach(([dt, dd]) => {
              const dterm = document.createElement("dt");
              dterm.textContent = dt;
              const ddef = document.createElement("dd");
              ddef.textContent = dd;
              dl.appendChild(dterm);
              dl.appendChild(ddef);
            });
            posContentEl.innerHTML = "";
            posContentEl.appendChild(dl);
          }

          function renderKnpmLabel(knpm) {
            if (!knpm) {
              resetKnpmView();
              return;
            }

            // Status
            knpmStatusEl.className = "knpm-status";
            if (knpm.classification === "FIT_FOR_CONSUMPTION") {
              knpmStatusEl.textContent = "Fit for consumption";
              knpmStatusEl.classList.add("fit");
              // For now, we don't show a specific fit SVG (only status text).
              knpmOctagonsEl.innerHTML = "";
            } else if (knpm.classification === "LESS_HEALTHY") {
              knpmStatusEl.textContent = "Less healthy";
              knpmStatusEl.classList.add("less");
            } else {
              knpmStatusEl.textContent = knpm.message || "KNPM classification not available.";
              knpmStatusEl.classList.add("unknown");
            }

            // Octagons
            if (knpm.classification !== "FIT_FOR_CONSUMPTION") {
              knpmOctagonsEl.innerHTML = "";
              if (Array.isArray(knpm.octagons) && knpm.octagons.length > 0) {
                const srcMap = {
                  HIGH_IN_SUGAR: "/octagon_images/high_in_sugar.svg",
                  HIGH_IN_SALT: "/octagon_images/high_in_salt.svg",
                  HIGH_IN_FAT: "/octagon_images/high_in_fat.svg"
                };
                const labelMap = {
                  HIGH_IN_SUGAR: "High in sugar",
                  HIGH_IN_SALT: "High in salt",
                  HIGH_IN_FAT: "High in fat"
                };
                knpm.octagons.forEach(code => {
                  const wrapper = document.createElement("div");
                  wrapper.className = "octagon-wrapper";
                  const frame = document.createElement("div");
                  frame.className = "octagon-frame";
                  const img = document.createElement("img");
                  img.className = "octagon-img";
                  img.dataset.code = code;
                  img.alt = labelMap[code] || code.replace(/_/g, " ");
                  img.src = srcMap[code] || srcMap.HIGH_IN_FAT;
                  frame.appendChild(img);
                  wrapper.appendChild(frame);
                  const caption = document.createElement("div");
                  caption.className = "octagon-label";
                  caption.textContent = labelMap[code] || code.replace(/_/g, " ");
                  wrapper.appendChild(caption);
                  knpmOctagonsEl.appendChild(wrapper);
                });
              }
            }

            // Reasons
            knpmReasonsEl.innerHTML = "";
            if (Array.isArray(knpm.reasons) && knpm.reasons.length > 0) {
              knpm.reasons.forEach(r => {
                const li = document.createElement("li");
                li.textContent = r;
                knpmReasonsEl.appendChild(li);
              });
            }

            // If we have a message (e.g. missing nutrition), surface it as a final note.
            if (knpm.message) {
              const li = document.createElement("li");
              li.textContent = knpm.message;
              knpmReasonsEl.appendChild(li);
            }
          }

          form.addEventListener("submit", async (event) => {
            event.preventDefault();
            const formData = new FormData();

            // Prefer camera image if provided, otherwise fall back to file upload
            const cameraFile = cameraInput ? cameraInput.files[0] : null;
            const file = fileInput ? fileInput.files[0] : null;

            if (cameraFile) {
              formData.append("image", cameraFile);
            } else if (file) {
              formData.append("image", file);
            } else {
              resultEl.textContent = "Please select or capture an image first.";
              return;
            }

            resultEl.textContent = "Processing...";
            resetKnpmView();
            resetPosView();
            try {
              const response = await fetch("/extract", {
                method: "POST",
                body: formData
              });

              if (!response.ok) {
                const text = await response.text();
                resultEl.textContent = "Server error " + response.status + ": " + text;
                resetKnpmView();
                resetPosView();
                return;
              }

              const data = await response.json();
              resultEl.textContent = JSON.stringify(data, null, 2);
              renderKnpmLabel(data.knpm_label);
              renderSupermarketClassification(data.supermarket_classification);
            } catch (err) {
              resultEl.textContent = "Error: " + err;
              resetKnpmView();
              resetPosView();
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

