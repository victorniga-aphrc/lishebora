#!/usr/bin/env python3
"""
Build a slim lookup table from data/huge_data.csv for POS product → class/subclass.

Output: data/product_class_subclass_lookup.csv
Columns: description, class_name, subclass_name, nova

By default, pack sizes and common packaging tokens are stripped from ``description``,
then rows are deduplicated on the **normalized** text (first row wins). That aligns
with nutrition being per 100g/100ml: the same product in different pack sizes maps
to one lookup line.

Use ``--no-strip-pack`` to keep raw POS descriptions and only dedupe exact strings.

Usage:
  python scripts/build_product_classification_lookup.py
  python scripts/build_product_classification_lookup.py --source data/huge_data.csv --out data/product_class_subclass_lookup.csv
  python scripts/build_product_classification_lookup.py --report-conflicts
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

_COL_ROOT = Path(__file__).resolve().parent.parent
if str(_COL_ROOT) not in sys.path:
    sys.path.insert(0, str(_COL_ROOT))

from app.utils.pos_description import normalize_pack_description

COLS = ["description", "class_name", "subclass_name", "nova"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Trim huge_data → product_class_subclass_lookup.csv")
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "huge_data.csv",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "product_class_subclass_lookup.csv",
    )
    parser.add_argument("--report-conflicts", action="store_true")
    parser.add_argument(
        "--no-strip-pack",
        action="store_true",
        help="Do not strip sizes/pack tokens; dedupe only on exact description text.",
    )
    args = parser.parse_args()

    rows: list[dict[str, str]] = []
    with args.source.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        missing = [c for c in COLS if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"Source missing columns {missing}; have {reader.fieldnames}")
        for row in reader:
            rows.append({c: (row.get(c) or "").strip() for c in COLS})

    by_key: dict[str, dict[str, str]] = {}
    conflicts = 0
    stripped_dupes = 0
    seen_raw_for_key: dict[str, str] = {}

    for row in rows:
        d = row["description"]
        if not d:
            continue
        if args.no_strip_pack:
            key = d
            out_desc = d
        else:
            out_desc = normalize_pack_description(d)
            if not out_desc:
                continue
            key = out_desc

        if key not in by_key:
            by_key[key] = {
                "description": out_desc,
                "class_name": row["class_name"],
                "subclass_name": row["subclass_name"],
                "nova": row["nova"],
            }
            seen_raw_for_key[key] = d
        else:
            prev = by_key[key]
            key_prev = (prev["class_name"], prev["subclass_name"], prev["nova"])
            key_new = (row["class_name"], row["subclass_name"], row["nova"])
            if key_prev != key_new:
                conflicts += 1
                if args.report_conflicts:
                    print(
                        "CONFLICT:",
                        repr(key)[:100],
                        key_prev,
                        "vs",
                        key_new,
                        "| raw:",
                        repr(seen_raw_for_key.get(key, ""))[:60],
                        "vs",
                        repr(d)[:60],
                    )
            else:
                stripped_dupes += 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for row in sorted(by_key.values(), key=lambda x: x["description"].lower()):
            w.writerow(row)

    nonempty = sum(1 for r in rows if r["description"])
    print(f"Source rows: {len(rows)}")
    print(f"Rows with description: {nonempty}")
    print(f"Unique lookup rows written: {len(by_key)}")
    print(f"Extra rows merged (same key, same taxonomy): {stripped_dupes}")
    print(f"Conflicts (same normalized key, different class/subclass/nova): {conflicts}")
    print(f"Strip pack tokens: {not args.no_strip_pack}")
    print(f"Written: {args.out}")


if __name__ == "__main__":
    main()
