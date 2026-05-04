"""Quick inspector for catalog.classification_cache."""
from __future__ import annotations
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import psycopg2
from dotenv import load_dotenv

load_dotenv()
url = os.getenv("DATABASE_URL")
if not url:
    sys.exit("DATABASE_URL not set")

conn = psycopg2.connect(url)
try:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, cache_key, source, model_used, class_name, subclass_name,
                   confidence, needs_review, reason, created_at, updated_at
            FROM catalog.classification_cache
            ORDER BY created_at DESC
            LIMIT 25
            """
        )
        rows = cur.fetchall()
    print(f"Total recent rows: {len(rows)}\n")
    for r in rows:
        (rid, key, src, model, cls, sub, conf, review, reason, c_at, u_at) = r
        print(f"#{rid} key={key!r}")
        print(f"   source={src} model={model} conf={conf} needs_review={review}")
        print(f"   class={cls!r} subclass={sub!r}")
        print(f"   reason={reason!r}")
        print(f"   created_at={c_at} updated_at={u_at}")
        print()
finally:
    conn.close()
