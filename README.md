## Lishebora VIC Backend

This repository contains the backend and experimentation code for the Lishebora nutrition labelling tool.

For now, the focus is on building an image intake and OCR pipeline using FastAPI and Replicate to extract clean ingredient lists from food package images.

### Quick start (development)

1. **Create and activate a Python environment**

```bash
cd /mnt/d/aphrc/lishebora_vic
python -m venv .venv
source .venv/bin/activate  # On Windows PowerShell: .venv\Scripts\Activate.ps1
```

2. **Install dependencies**

```bash
pip install -r requirements.txt
```

3. **Set environment variables**

Create a `.env` file (already excluded from git) with at least:

```bash
REPLICATE_API_TOKEN=your_replicate_token_here
```

4. **Run the development server**

```bash
uvicorn app.main:app --reload
```

5. **Open the demo web page**

Visit `http://localhost:8000` in your browser to upload a sample image and see the structured response.

