#!/usr/bin/env python3
"""
Build a cleaned reference nutrition table from data/all_categories_combined.csv.

Steps:
  - Drop junk / retail-only columns (price, unnamed indexes, etc.).
  - Normalize product names with the same pack-size rules as catalog matching
    (app.utils.pack_description.normalize_pack_description).
  - Parse numeric nutrients (strip kcal, kJ, g, mg); sodium stored as g/100g like the app.
  - Interpret PortionType internally: per-100g/ml as-is; scale per-Xg rows to per-100g;
    per-serving / unknown → no scaling (values left empty where scaling is unsafe).
  - Deduplicate by normalized product_name (prefer richer rows, then clearer portion basis).

Output: data/reference_nutrition_lookup.csv (product_name, numeric nutrients, sub_type, form only)

Usage:
  python scripts/build_reference_nutrition_lookup.py
  python scripts/build_reference_nutrition_lookup.py --source data/all_categories_combined.csv --out data/reference_nutrition_lookup.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.utils.pack_description import normalize_pack_description

# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

_NUM = re.compile(r"(-?\d+(?:\.\d+)?)")


def parse_numeric_cell(raw: str | None) -> float | None:
    """Extract first number; convert mg → g for sodium-style cells."""
    if raw is None:
        return None
    s = str(raw).strip().lower().replace(",", "")
    if not s or s in ("-", "—", "na", "n/a"):
        return None
    m = _NUM.search(s)
    if not m:
        return None
    val = float(m.group(1))
    if "mg" in s:
        return val / 1000.0
    return val


def parse_portion_type(raw: str | None) -> tuple[str, float | None]:
    """
    Return (basis_code, grams_basis).

    basis_code: PER_100G | PER_100ML | PER_XG_RAW | SERVING_OR_UNKNOWN | EMPTY
    grams_basis: for PER_XG_RAW, the X in "per X g" before scaling to 100g.
    """
    t = (raw or "").strip().upper()
    if not t:
        return ("EMPTY", None)

    if "SERVING" in t:
        return ("SERVING_OR_UNKNOWN", None)

    if "100ML" in t or re.search(r"PER\s+100\s*ML", t):
        return ("PER_100ML", None)

    if t == "100G" or "100G" in t or re.search(r"PER\s+100\s*G", t):
        return ("PER_100G", None)

    if "VALUES AS PER 100G" in t or "VALUE AS PER 100G" in t:
        return ("PER_100G", None)

    m = re.search(r"PER\s*(\d+(?:\.\d+)?)\s*G", t)
    if m:
        g = float(m.group(1))
        if g > 0:
            return ("PER_XG_RAW", g)

    m = re.search(r"PER\s*(\d+(?:\.\d+)?)\s*ML", t)
    if m:
        return ("SERVING_OR_UNKNOWN", None)

    if "1CUP" in t.replace(" ", "") or "TABLESPOON" in t:
        return ("SERVING_OR_UNKNOWN", None)

    return ("SERVING_OR_UNKNOWN", None)


def scale_to_per_100g(
    basis: str,
    grams_basis: float | None,
    value: float | None,
) -> float | None:
    if value is None:
        return None
    if basis == "PER_100G":
        return value
    if basis == "PER_100ML":
        return value
    if basis == "PER_XG_RAW" and grams_basis and grams_basis > 0:
        return value * (100.0 / grams_basis)
    return None


def completeness_score(rec: dict[str, object]) -> int:
    keys = [
        "energy_kcal",
        "protein_g",
        "carbohydrates_g",
        "total_sugar_g",
        "total_fat_g",
        "fibre_g",
        "sodium_g",
    ]
    return sum(1 for k in keys if rec.get(k) is not None)


def basis_rank(basis: str) -> int:
    """Lower is better for conflict resolution."""
    order = {
        "PER_100G": 0,
        "PER_100ML": 1,
        "PER_XG_RAW": 2,
        "SERVING_OR_UNKNOWN": 5,
        "EMPTY": 6,
    }
    return order.get(basis, 4)


OUT_FIELDS = [
    "product_name",
    "energy_kcal",
    "energy_kj",
    "protein_g",
    "carbohydrates_g",
    "total_sugar_g",
    "total_fat_g",
    "fibre_g",
    "sodium_g",
    "sub_type",
    "form",
]


def row_to_record(row: dict[str, str]) -> dict[str, object]:
    food = (row.get("Food Name") or "").strip()
    product_name = normalize_pack_description(food) if food else ""

    basis, grams_basis = parse_portion_type(row.get("PortionType"))

    energy_kcal_raw = parse_numeric_cell(row.get("Energyinkcal"))
    energy_kj_raw = parse_numeric_cell(row.get("EnergyinkJ"))
    protein_raw = parse_numeric_cell(row.get("Protein"))
    carb_raw = parse_numeric_cell(row.get("Carbohydrates"))
    sugar_raw = parse_numeric_cell(row.get("Sugar"))
    fat_raw = parse_numeric_cell(row.get("Fat"))
    fibre_raw = parse_numeric_cell(row.get("Fibre"))
    sodium_raw = parse_numeric_cell(row.get("Sodium"))

    # Many rows omit PortionType but still list per-100g style values; assume per 100g.
    if basis == "EMPTY" and energy_kcal_raw is not None:
        basis = "PER_100G"
        grams_basis = None

    ek = scale_to_per_100g(basis, grams_basis, energy_kcal_raw)
    ej = scale_to_per_100g(basis, grams_basis, energy_kj_raw)
    pg = scale_to_per_100g(basis, grams_basis, protein_raw)
    cg = scale_to_per_100g(basis, grams_basis, carb_raw)
    sg = scale_to_per_100g(basis, grams_basis, sugar_raw)
    fg = scale_to_per_100g(basis, grams_basis, fat_raw)
    fib = scale_to_per_100g(basis, grams_basis, fibre_raw)
    sod = scale_to_per_100g(basis, grams_basis, sodium_raw)

    return {
        "product_name": product_name,
        "energy_kcal": ek,
        "energy_kj": ej,
        "protein_g": pg,
        "carbohydrates_g": cg,
        "total_sugar_g": sg,
        "total_fat_g": fg,
        "fibre_g": fib,
        "sodium_g": sod,
        "sub_type": (row.get("Sub Type") or "").strip(),
        "form": (row.get("Form") or "").strip(),
        "_basis_for_rank": basis,
        "_completeness": completeness_score(
            {
                "energy_kcal": ek,
                "protein_g": pg,
                "carbohydrates_g": cg,
                "total_sugar_g": sg,
                "total_fat_g": fg,
                "fibre_g": fib,
                "sodium_g": sod,
            }
        ),
    }


def better_record(a: dict, b: dict) -> dict:
    """Prefer higher completeness, then better portion basis."""
    ca, cb = a["_completeness"], b["_completeness"]
    if ca != cb:
        return a if ca > cb else b
    ra, rb = basis_rank(a["_basis_for_rank"]), basis_rank(b["_basis_for_rank"])
    if ra != rb:
        return a if ra < rb else b
    return a


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean all_categories_combined → reference_nutrition_lookup.csv")
    parser.add_argument(
        "--source",
        type=Path,
        default=_ROOT / "data" / "all_categories_combined.csv",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=_ROOT / "data" / "reference_nutrition_lookup.csv",
    )
    parser.add_argument("--report-conflicts", action="store_true")
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"Source not found: {args.source}")

    by_name: dict[str, dict] = {}
    conflicts = 0
    skipped_empty_name = 0
    total_in = 0

    with args.source.open(newline="", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total_in += 1
            rec = row_to_record(row)
            name = rec["product_name"]
            if not name:
                skipped_empty_name += 1
                continue

            if name not in by_name:
                by_name[name] = rec
            else:
                prev = by_name[name]
                key_prev = (
                    prev["_basis_for_rank"],
                    prev["_completeness"],
                    prev.get("total_sugar_g"),
                )
                key_new = (
                    rec["_basis_for_rank"],
                    rec["_completeness"],
                    rec.get("total_sugar_g"),
                )
                if key_prev != key_new:
                    conflicts += 1
                    if args.report_conflicts:
                        print("DUP:", name[:70], key_prev, "||", key_new)
                chosen = better_record(prev, rec)
                by_name[name] = chosen

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=OUT_FIELDS, extrasaction="ignore")
        w.writeheader()
        for rec in sorted(by_name.values(), key=lambda x: x["product_name"].lower()):
            out = {k: rec[k] for k in OUT_FIELDS}
            # Normalise floats for CSV (empty string for None)
            for k, v in list(out.items()):
                if isinstance(v, float):
                    out[k] = f"{v:.6g}" if v == v else ""  # NaN guard
                elif v is None:
                    out[k] = ""
            w.writerow(out)

    print(f"Source: {args.source}")
    print(f"Rows read: {total_in}")
    print(f"Unique product_name after dedupe: {len(by_name)}")
    print(f"Skipped empty normalized name: {skipped_empty_name}")
    print(f"Duplicate name merges (different detail): {conflicts}")
    print(f"Written: {args.out}")


if __name__ == "__main__":
    main()
