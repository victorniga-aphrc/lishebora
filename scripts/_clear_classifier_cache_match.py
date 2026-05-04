"""Delete classification_cache rows whose product_name or cache_key contains a substring.

Use this after expanding the OpenAI evidence block / prompt so previously cached
"no labels" predictions don't short-circuit the new pipeline.

Usage:
    PYTHONPATH=. python scripts/_clear_classifier_cache_match.py orchid
"""
from __future__ import annotations

import sys

from sqlalchemy import text

from app.config import settings
from app.db import engine


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: _clear_classifier_cache_match.py <substring>")
        sys.exit(2)
    needle = sys.argv[1].strip().lower()
    if not needle:
        print("empty substring; aborting")
        sys.exit(2)
    table = settings.classification_cache_qualified_sql
    with engine.begin() as conn:
        deleted = conn.execute(
            text(
                f"DELETE FROM {table} "
                "WHERE LOWER(product_name) LIKE :pat OR LOWER(cache_key) LIKE :pat "
                "RETURNING cache_key, source"
            ),
            {"pat": f"%{needle}%"},
        ).fetchall()
        remaining = conn.execute(
            text(
                f"SELECT id, cache_key, source, model_used FROM {table} ORDER BY id"
            )
        ).fetchall()
    print(f"deleted {len(deleted)} row(s) matching {needle!r}:")
    for r in deleted:
        print("  -", r.cache_key, "(", r.source, ")")
    print(f"remaining: {len(remaining)}")
    for r in remaining:
        print("  -", r.id, r.cache_key, r.source, r.model_used)


if __name__ == "__main__":
    main()
