from fastapi import Depends, FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from pydantic import BaseModel

from app.db import get_db
from app.models import HealthierSubstituteResult, OcrResult
from app.services.db_service import save_ocr_result_to_db
from app.services.ocr_client import OcrClientError, extract_ingredients_from_image
from app.services.recommendation_explainer import attach_healthier_recommendations


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
          .subs-card { border: 1px solid #e5e7eb; border-radius: 0.75rem; padding: 1.25rem; background: #eff6ff; }
          .subs-header { font-weight: 600; margin-bottom: 0.5rem; }
          .subs-meta { font-size: 0.85rem; color: #4b5563; margin-bottom: 0.75rem; line-height: 1.45; }
          .subs-warn-banner {
            font-size: 0.8rem; color: #92400e; background: #fffbeb; border: 1px solid #fcd34d;
            border-radius: 0.375rem; padding: 0.5rem 0.65rem; margin-bottom: 0.75rem;
          }
          .subs-tags { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-bottom: 0.65rem; }
          .subs-tag {
            font-size: 0.7rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.02em;
            background: #e0e7ff; color: #3730a3; padding: 0.2rem 0.45rem; border-radius: 0.25rem;
          }
          .subs-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.65rem; }
          .subs-item {
            background: #fff; border: 1px solid #dbeafe; border-radius: 0.5rem; padding: 0.65rem 0.75rem;
            font-size: 0.88rem; color: #1e293b;
          }
          .subs-item-title { font-weight: 600; color: #0f172a; margin-bottom: 0.35rem; line-height: 1.35; }
          .subs-item-row { font-size: 0.8rem; color: #64748b; display: flex; flex-wrap: wrap; gap: 0.35rem 0.75rem; align-items: center; }
          .subs-pill {
            display: inline-block; font-size: 0.7rem; font-weight: 600; padding: 0.15rem 0.4rem; border-radius: 0.25rem;
          }
          .subs-pill-tier { background: #dbeafe; color: #1d4ed8; }
          .subs-pill-ok { background: #dcfce7; color: #166534; }
          .subs-pill-warn { background: #fee2e2; color: #991b1b; }
          .subs-oct { font-size: 0.75rem; color: #64748b; }
          .subs-explanation {
            font-size: 0.88rem; color: #334155; line-height: 1.5; margin-top: 0.75rem; padding-top: 0.75rem;
            border-top: 1px solid #bfdbfe;
          }
          .subs-footnote { font-size: 0.72rem; color: #64748b; margin-top: 0.65rem; line-height: 1.4; }
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
            <div class="subs-card">
              <div class="subs-header">Healthier substitutes</div>
              <div id="subs-content" class="pos-none">No scan yet.</div>
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
          const subsContentEl = document.getElementById("subs-content");

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

          function resetSubstitutesView() {
            subsContentEl.className = "pos-none";
            subsContentEl.textContent = "No scan yet.";
          }

          function renderHealthierSubstitutes(hs) {
            subsContentEl.className = "";
            subsContentEl.innerHTML = "";
            if (!hs) {
              subsContentEl.className = "pos-none";
              subsContentEl.textContent = "No healthier_substitutes in response (disabled or not returned).";
              return;
            }
            if (!hs.triggered) {
              subsContentEl.className = "pos-meta";
              const p = document.createElement("p");
              p.className = "subs-meta";
              p.style.margin = "0";
              p.textContent = hs.skip_reason || "Substitutes not suggested for this product.";
              subsContentEl.appendChild(p);
              return;
            }

            const wrap = document.createElement("div");

            if (Array.isArray(hs.exceeded_nutrient_summary) && hs.exceeded_nutrient_summary.length > 0) {
              const tagLabel = document.createElement("div");
              tagLabel.className = "subs-meta";
              tagLabel.textContent = "Concern tags (for this scan):";
              wrap.appendChild(tagLabel);
              const tags = document.createElement("div");
              tags.className = "subs-tags";
              hs.exceeded_nutrient_summary.forEach((t) => {
                const span = document.createElement("span");
                span.className = "subs-tag";
                span.textContent = String(t).replace(/_/g, " ");
                tags.appendChild(span);
              });
              wrap.appendChild(tags);
            }

            const meta = document.createElement("div");
            meta.className = "subs-meta";
            meta.textContent =
              "Tier used: " + (hs.tier_used != null ? String(hs.tier_used) : "—") +
              " (1 = same subclass, 2 = same class, 3 = wider catalog).";
            wrap.appendChild(meta);

            if (hs.inferred_scan_form) {
              const formRow = document.createElement("div");
              formRow.className = "subs-meta";
              formRow.textContent =
                "Inferred pack form for this scan: " + hs.inferred_scan_form +
                " — substitutes prefer the same form when the catalog lists it.";
              wrap.appendChild(formRow);
            }

            if (hs.inferred_substitute_use_context === "beverage_drink") {
              const ctx = document.createElement("div");
              ctx.className = "subs-meta";
              ctx.textContent =
                "Use context: beverage / drink — oils and vinegars are ranked last (or omitted when enough drink options exist).";
              wrap.appendChild(ctx);
            }

            if (hs.substitutes_include_pantry_liquids) {
              const oilBanner = document.createElement("div");
              oilBanner.className = "subs-warn-banner";
              oilBanner.textContent =
                "Some rows are pantry liquids (e.g. oil) — shown only because fewer drink-style products met the same KNPM limits.";
              wrap.appendChild(oilBanner);
            }

            if (hs.substitutes_include_other_forms) {
              const formBanner = document.createElement("div");
              formBanner.className = "subs-warn-banner";
              formBanner.textContent =
                "Some suggestions are a different form (e.g. solid vs drink) because few same-form products met the criteria.";
              wrap.appendChild(formBanner);
            }

            if (hs.no_close_substitutes) {
              const banner = document.createElement("div");
              banner.className = "subs-warn-banner";
              banner.textContent =
                "Search was widened and/or no fully below-threshold matches in the closest category — see tier note above.";
              wrap.appendChild(banner);
            }

            const subs = hs.substitutes;
            if (!Array.isArray(subs) || subs.length === 0) {
              const empty = document.createElement("p");
              empty.className = "subs-meta";
              empty.style.margin = "0";
              empty.textContent = hs.skip_reason || "No substitute rows returned from the reference catalog.";
              wrap.appendChild(empty);
            } else {
              const list = document.createElement("ul");
              list.className = "subs-list";
              subs.forEach((s) => {
                const li = document.createElement("li");
                li.className = "subs-item";
                const title = document.createElement("div");
                title.className = "subs-item-title";
                title.textContent = s.product_name || "—";
                li.appendChild(title);
                const row = document.createElement("div");
                row.className = "subs-item-row";
                const tier = document.createElement("span");
                tier.className = "subs-pill subs-pill-tier";
                tier.textContent = "Tier " + (s.tier != null ? s.tier : "?");
                row.appendChild(tier);
                const ok = document.createElement("span");
                ok.className = "subs-pill " + (s.below_knpm_thresholds ? "subs-pill-ok" : "subs-pill-warn");
                ok.textContent = s.below_knpm_thresholds ? "Below KNPM limits" : "Has warnings";
                row.appendChild(ok);
                if (s.form) {
                  const fp = document.createElement("span");
                  fp.className = "subs-pill subs-pill-tier";
                  fp.style.background = "#e0e7ff";
                  fp.textContent = "Form: " + s.form;
                  row.appendChild(fp);
                }
                if (s.sub_type) {
                  const st = document.createElement("span");
                  st.className = "subs-oct";
                  st.style.fontWeight = "600";
                  st.textContent = "Type: " + s.sub_type;
                  row.appendChild(st);
                }
                const oc = document.createElement("span");
                oc.className = "subs-oct";
                oc.textContent =
                  "Black octagons: " + (s.octagon_count != null ? s.octagon_count : "—") +
                  (Array.isArray(s.octagons) && s.octagons.length
                    ? " (" + s.octagons.map((c) => c.replace(/_/g, " ").toLowerCase()).join(", ") + ")"
                    : "");
                row.appendChild(oc);
                li.appendChild(row);
                if (s.class_name || s.subclass_name) {
                  const tax = document.createElement("div");
                  tax.className = "subs-oct";
                  tax.style.marginTop = "0.25rem";
                  tax.textContent =
                    [s.class_name && "Class: " + s.class_name, s.subclass_name && "Subclass: " + s.subclass_name]
                      .filter(Boolean)
                      .join(" · ");
                  li.appendChild(tax);
                }
                list.appendChild(li);
              });
              wrap.appendChild(list);
            }

            if (hs.explanation && String(hs.explanation).trim()) {
              const exp = document.createElement("div");
              exp.className = "subs-explanation";
              exp.textContent = hs.explanation;
              wrap.appendChild(exp);
            }

            if (hs.approach_note) {
              const foot = document.createElement("div");
              foot.className = "subs-footnote";
              foot.textContent = hs.approach_note;
              wrap.appendChild(foot);
            }

            subsContentEl.appendChild(wrap);
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
            resetSubstitutesView();
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
                resetSubstitutesView();
                return;
              }

              const data = await response.json();
              resultEl.textContent = JSON.stringify(data, null, 2);
              renderKnpmLabel(data.knpm_label);
              renderSupermarketClassification(data.supermarket_classification);
              renderHealthierSubstitutes(data.healthier_substitutes);
            } catch (err) {
              resultEl.textContent = "Error: " + err;
              resetKnpmView();
              resetPosView();
              resetSubstitutesView();
            }
          });
        </script>
      </body>
    </html>
    """


class SubstituteRecommendRequest(BaseModel):
    """Prior scan JSON plus optional shopper goal for the substitute explainer."""

    ocr_result: OcrResult
    user_goal: str | None = None


@app.post("/extract", response_model=OcrResult)
async def extract(
    image: UploadFile = File(...),
    user_goal: str | None = Form(None),
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
        result = await extract_ingredients_from_image(image_bytes, user_goal=user_goal)
        
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


@app.post("/recommend/substitutes", response_model=HealthierSubstituteResult)
async def recommend_substitutes(payload: SubstituteRecommendRequest) -> HealthierSubstituteResult:
    """
    Recompute healthier substitutes + explanation from a prior ``OcrResult`` JSON
    (e.g. after editing ``user_goal`` without re-running vision OCR).
    """
    updated = await attach_healthier_recommendations(
        payload.ocr_result,
        has_trans_fats=False,
        has_sweeteners=False,
        user_goal=payload.user_goal,
    )
    hs = updated.healthier_substitutes
    if hs is None:
        return HealthierSubstituteResult(
            triggered=False,
            skip_reason="Substitute recommendations disabled.",
        )
    return hs


@app.get("/health", response_class=JSONResponse)
async def health() -> dict:
    """Simple health check endpoint."""
    return {"status": "ok"}

