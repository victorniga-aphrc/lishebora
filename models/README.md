# ML artifacts — NOVA / taxonomy model

## `novaclasses_model.pkl`

- **Format**: Python `pickle` of a **Keras 3** `keras.src.models.functional.Functional` model.
- **Architecture (confirmed via `inspect_novaclasses_model.py`)**:
  - **Input:** one tensor **`(None, 12)`**, **`int32`** — twelve token IDs per product name (fixed length).
  - **Layers:** `Embedding` (128-dim) → **BiLSTM** → **`nova_output` Dense**.
  - **Output:** **single head** **`(None, 4)`** — **4-way NOVA** (not separate class/subclass heads in this checkpoint).
  - The pickle may still contain legacy strings like `class_name` from training metadata; the **saved graph** is **NOVA-only**.
  - Embedding size **314,880** params ⇒ vocab ≈ **2,460** tokens (314880 ÷ 128).

## Dependencies (for loading the pickle)

The pickle imports **`keras`** when unpickling. Use a **supported Python** for TensorFlow (typically **3.10–3.12**). **Python 3.14** often has **no TensorFlow wheel** yet.

```bash
pip install -r requirements.txt
```

(`requirements-ml.txt` is a thin `-r requirements.txt` alias if you used the old filename.)

Then:

```bash
python scripts/inspect_novaclasses_model.py
python scripts/inspect_novaclasses_model.py --quick   # shorter: I/O + dummy predict only
```

**Step-by-step testing guide (before pipeline):** [docs/TESTING_NOVACLASSES_MODEL.md](../docs/TESTING_NOVACLASSES_MODEL.md)

## Expected inference contract (to confirm after inspect)

| Item | Status |
|------|--------|
| **Input** | **12 × int32** token IDs per row — **tokenizer** from training must match (vocab ~2460). |
| **Output** | **One softmax head, 4 units** → NOVA category index; you need **`nova_labels.json`** (or similar) mapping `0..3` to labels (e.g. “Unprocessed”, …). |

### Runtime files (for `/extract` and `scripts/predict_nova_cli.py`)

| File | Role |
|------|------|
| `novaclasses_model.pkl` | Keras model (in repo) |
| **`tokenizer.pkl`** | Tokenizer fitted on training names (`keras.preprocessing.text.Tokenizer` with `texts_to_sequences`). |
| `nova_labels.json` | Preferred index → label map for the **4** outputs. |
| `label_encoders.pkl` | Optional fallback: loader reads NOVA classes from common keys (`nova`, `nova_output`, `nova_class`) when `nova_labels.json` is missing. |

Enable inference:

```bash
export NOVA_BILSTM_ENABLED=true
# optional: copy BiLSTM NOVA into POS when CSV has no nova column
export NOVA_BILSTM_FILL_POS_NOVA=true
```

If tokenizer artifacts are missing, the API skips BiLSTM prediction. **Class/subclass** still come from **`supermarket_lookup`**.

## Integration (later)

The FastAPI app will call a small service that: normalizes product name → tokenize → `model.predict` → map indices to strings. **TensorFlow is listed in `requirements.txt`** with the rest of the stack. If you must deploy an image **without** ML, use a separate constraints file or install from a trimmed requirements variant in CI/Docker.
