# Mobile Frontend Update Guide

A handoff for the mobile app developer. This lists the backend changes we made on our end
and what (if anything) needs to change in the Flutter frontend to surface them.

All of this concerns the OCR image-scan flow:

- **Endpoint:** `POST /api/v1/scan/image` (multipart form)
  - `image` — the label photo (required; `image/jpeg`, `image/png`, `image/jpg`, `image/webp`; max 10MB)
  - `user_id` — optional; when sent, the scan is recorded in the user's history
  - `barcode` — optional; used to auto-save a barcode→product mapping on a confident match
- **Response:** `ProductScanResponse` (shape unchanged; see fields below)

No breaking changes — everything here is either already in the response or additive.

---

## 1. OCR confidence — make it visible in the UI

The scan response already includes the vision model's self-reported confidence in the
extraction, under the top-level `extraction_confidence` object. This is the field we want
surfaced on the result screen.

### Fields

| Field | Type | Range | Notes |
|-------|------|-------|-------|
| `extraction_confidence` | `object \| null` | — | `null` for barcode lookups; present for `/scan/image`. |
| `extraction_confidence.overall` | `number \| null` | `0.0`–`1.0` | Model's overall confidence in the whole extraction. |
| `extraction_confidence.by_field` | `object` | values `0.0`–`1.0` | Optional per-section confidence. Keys present only for sections considered: `ingredients`, `nutrition_facts`, `product_name`, `barcode`. May be `{}`. |

### Example response slice

```json
{
  "source": "ocr",
  "extraction_confidence": {
    "overall": 0.82,
    "by_field": {
      "ingredients": 0.9,
      "nutrition_facts": 0.78,
      "product_name": 0.95
    }
  }
}
```

### What the frontend should do

1. **Show an overall confidence indicator** on the scan result screen.
   - Value is a fraction `0.0`–`1.0`. Display as a percentage, e.g. `(c * 100).round()` → `82%`.
   - Suggested thresholds for color/label:
     - `>= 0.8` → High (green) — "High confidence"
     - `0.5 – 0.79` → Medium (amber) — "Review recommended"
     - `< 0.5` → Low (red) — "Low confidence, please rescan"
2. **Handle `null` gracefully.** If `extraction_confidence` is `null` or `overall` is `null`,
   hide the badge (don't show `0%`). It will always be `null` for barcode scans.
3. **(Optional) per-section confidence.** Use `by_field` to annotate each detected section
   (e.g. a small badge by the ingredients block or nutrition table). Only render badges for
   keys that are present.
4. **Encourage a rescan on low confidence** (better lighting, less glare, flatten the label).

> These are the model's *self-assessed* confidences — a quality hint for the user, not a guarantee.

---

## 2. Scan images are now saved and returned (changed on our end)

**What we changed in the backend:**

- **Every scan image is now stored** — previously the image was saved **only when a
  `user_id` was supplied**, so anonymous scans were silently discarded. Now both are kept:
  - logged-in: `/uploads/scan_images/user_{user_id}/<uuid>.jpg`
  - anonymous: `/uploads/scan_images/anonymous/<uuid>.jpg`
- **Images are sanitized before storage**: EXIF/GPS metadata stripped (privacy), auto-oriented,
  downscaled (longest edge ~1600px), and re-encoded to JPEG.
- The stored image's URL is returned in **`product.image_url`** and served as a static file
  (also under `/api/v1/uploads/...`).

### Where to find it in the response

```json
{
  "product": {
    "name": "Potato Crisps",
    "image_url": "/uploads/scan_images/anonymous/9f2c4a7b1e2d4f3a8c5b6d7e8f9a0b1c.jpg",
    "nutrients": { "total_fat": 35.0, "sodium": 1.2 }
  },
  "source": "ocr"
}
```

### What the frontend should do

1. **Build the full URL.** `product.image_url` is **relative** — prepend your configured API
   base (e.g. `https://api.example.org` + `image_url`) before loading it.
2. **Display / cache the server-stored image** on the result screen and in scan history,
   instead of holding the original capture in app memory.
3. **Handle `null`.** If `product.image_url` is absent, fall back to the locally captured
   photo (or hide the thumbnail). The scan result is still valid.

> The image sent to OCR is still the full-resolution capture; only the *stored* copy is
> downscaled/cleaned. What you fetch back is a clean JPEG (EXIF/orientation tags removed).

---

## 3. Barcode not found → prompt to scan the nutrition label

When a barcode lookup fails, the app should guide the user to scan the nutrition table via
OCR instead of dead-ending on an error. Most of this is frontend, but we added a backend
hint so the branch is reliable.

### Two cases the frontend handles

1. **No barcode detected on the package at all** (scanner finds nothing — common for local
   products). This is purely frontend: after a scan timeout / "no code" result, prompt the
   user to take a photo of the nutrition label and call `POST /api/v1/scan/image`.
2. **Barcode detected but not in our database** → `GET /api/v1/products/{barcode}` returns
   **404** with a structured hint (see below).

### What we changed in the backend

The barcode 404 response body now carries a machine-readable fallback hint. **`detail` is now
an object on this 404, not a plain string** — update any code that assumed a string here.

```json
{
  "detail": {
    "message": "Product with barcode 3017620422003 not found in database",
    "barcode": "3017620422003",
    "suggested_action": "scan_nutrition_label",
    "fallback_endpoint": "/api/v1/scan/image"
  }
}
```

### What the frontend should do

- On a `404` from `GET /products/{barcode}`, read `detail.suggested_action`. If it equals
  `scan_nutrition_label`, show a prompt like *"We couldn't find this product. Scan the
  nutrition label instead?"* and on confirm, open the camera and POST to
  `detail.fallback_endpoint` (`/api/v1/scan/image`).
- Carry the scanned `barcode` into that follow-up call as the optional `barcode` form field —
  the backend uses it to auto-save a barcode→product mapping when the OCR result matches a
  reference product, so the next person who scans that barcode gets an instant hit.

---

## 4. Sign in with Google (Gmail login)

We added a backend endpoint so users can log in with their Google account. The flow:
the app does Google Sign-In, gets a Google **ID token**, and posts it to the backend, which
verifies it and returns the same logged-in user object as normal login.

### New endpoint

- **`POST /api/v1/auth/google`**
  - Body: `{ "id_token": "<google-id-token-from-the-app>" }`
  - Success: `200` with the standard `AuthResponse` (`success`, `message`, `user`) — identical
    shape to `/auth/login`, so existing post-login handling can be reused as-is.

The backend verifies the token with Google, then:
1. logs in the matching Google account, or
2. links Google to an existing account with the same verified email, or
3. creates a new account (no password; sign-in is via Google going forward).

### What Domian needs to do (the rest)

1. **Google Cloud Console:** create an OAuth 2.0 Client ID for each platform the app ships
   (Android, iOS, and/or Web). This is the "Google account for tokens" part — done in the
   Cloud Console, not in code.
2. **Give us the client IDs** so we can set `GOOGLE_CLIENT_IDS` (comma-separated) in the
   backend env. Until that's set, `POST /auth/google` returns `503` by design.
3. **Flutter app:**
   - Add a "Continue with Google" button using the `google_sign_in` package.
   - Configure it with the Google client ID(s).
   - After sign-in, read the **ID token** (`GoogleSignInAuthentication.idToken`).
   - `POST` it to `/api/v1/auth/google` as `{ "id_token": "..." }`.
   - On success, store the returned `user` exactly like email/password login.

### Error handling on the app side

- `401` → token invalid/expired or not issued for this app (usually a client-ID mismatch).
- `403` → Google email not verified, or account inactive.
- `503` → server `GOOGLE_CLIENT_IDS` not configured yet (waiting on step 2).

> Send the **ID token**, not the access token. The backend validates the ID token's
> signature, issuer, audience (client ID), and `email_verified` before trusting it.

---

## 5. Healthy products return no recommendations

We changed the backend so that when a scanned product is **declared healthy**
(`knpm_label.label_type == "fit_for_consumption"`), no "healthier alternatives" are
returned — there's nothing to improve on.

This applies everywhere recommendations come from:
- `POST /api/v1/scan/image` → `recommendations` is `[]` for a healthy product.
- `GET /api/v1/products/{barcode}/recommendations` → empty list for a healthy product.
- `GET /api/v1/products/search/recommendations` → empty list for a healthy product.

### What the frontend should do

- Only render the "Healthier alternatives" section when `recommendations` is non-empty.
- For a `fit_for_consumption` result, show the positive/healthy state (and you can skip
  calling the recommendations endpoint entirely for barcode scans).

---

## 6. Black KNPM octagons (unhealthy products)

When a product is **unhealthy** (`knpm_label.label_type == "black_octagon"`), the app should show
the black octagon warning graphics — same assets as the main Lishebora pipeline.

### What we added on the backend

1. **Octagon images copied** into Domian's backend at `octagon_images/`:
   - `high_in_sugar.svg`
   - `high_in_salt.svg`
   - `high_in_fat.svg`
2. **Served as static files** at:
   - `/octagon_images/<filename>.svg`
   - `/api/v1/octagon_images/<filename>.svg` (same files, under the API prefix)
3. **New field on every `knpm_label`:** `octagons` — a string array of warning codes. Empty when
   `label_type` is `fit_for_consumption`.

### Example (unhealthy scan)

```json
{
  "knpm_label": {
    "label_type": "black_octagon",
    "octagons": ["high_in_sugar", "high_in_salt"],
    "reasons": ["Total sugar (12.0 g/100g/ml) exceeds KNPM threshold (4.7 g/100g/ml)", "..."],
    "exceeds_thresholds": { "total_sugar": 12.0, "sodium": 0.5 }
  }
}
```

### Code → image mapping (for Flutter)

| `octagons` code | Label text | Image URL (relative) |
|----------------|------------|----------------------|
| `high_in_sugar` | High in sugar | `/octagon_images/high_in_sugar.svg` |
| `high_in_salt` | High in salt | `/octagon_images/high_in_salt.svg` |
| `high_in_fat` | High in fat | `/octagon_images/high_in_fat.svg` |

Prefix with your API base (e.g. `https://host:8000` + path). Same pattern as `product.image_url`.

### What the frontend should do

- When `knpm_label.label_type == "black_octagon"` and `knpm_label.octagons` is non-empty,
  render one icon per code (with the label text above).
- When `label_type == "fit_for_consumption"`, **do not** show octagons (show the healthy state instead).
- `knpm_label` appears on barcode scans, image scans, and each item in `recommendations[].knpm_label`.

---

## 7. Smarter recommendations (LLM category classification)

This is a **backend-only** improvement — no frontend change required — but worth knowing.

Recommendations rank alternatives by matching the scanned product's **category/subclass**.
Previously that only worked when OCR exactly matched a known reference product; otherwise it
fell back to coarse keyword groups. We added an **LLM classifier** that assigns the product a
`class_name` / `subclass_name` / `nova` from the taxonomy when OCR didn't resolve them, so
recommendations are more relevant (same-subclass first, then same-class).

- Runs automatically inside the recommendation flow; results are **cached** per product so
  repeat scans don't re-call the LLM.
- Controlled by backend env (`PRODUCT_CLASSIFIER_ENABLED`, `PRODUCT_CLASSIFIER_MODEL`,
  `PRODUCT_CLASSIFIER_REVIEW_THRESHOLD`).
- These taxonomy labels are **internal** — the existing guidance still applies: don't surface
  raw `class_name`/`subclass_name` in the UI. You only consume the resulting
  `recommendations` list, which already improves automatically.

No new fields to wire up; `recommendations[]` keeps the same shape.

---

## 8. Things to be aware of

- **Anonymous scans: image saved, but not in history.** The image file is stored and
  `product.image_url` is returned even without a `user_id`, but the scan only appears in a
  user's **scan history** (`/scan/image` event logging, recent scans, analytics) when a
  `user_id` is sent. If you want a scan to show up in history, pass `user_id`.
- **No auth on the image URL yet.** Stored images are served without authentication (matches
  the rest of the API in dev). Treat the URL as non-secret for now; auth will come with
  accounts.
- **`source` field.** `ocr` = label only; `ocr_reference_match` = OCR matched a reference
  product and the DB filled category/taxonomy and any missing nutrients.
- **`recommendations`** are returned inline on a scan when a product is identified (same item
  shape as the recommendations endpoint) — no behavior change, just a reminder it's there.

---

## Quick reference: fields the mobile model should read

```text
extraction_confidence.overall      -> double?  (0.0–1.0, nullable; null for barcode scans)
extraction_confidence.by_field     -> Map<String, double>  (may be empty)
product.image_url                  -> String?  (relative URL of stored scan image; prefix with API host)
```

Everything else in `ProductScanResponse` is unchanged.
