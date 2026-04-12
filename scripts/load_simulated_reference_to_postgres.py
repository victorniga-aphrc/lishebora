#!/usr/bin/env python3
"""Load reference_nutrition_lookup_simulated.csv into PostgreSQL (catalog.reference_products)."""

from __future__ import annotations

import csv
from pathlib import Path
import sys

from sqlalchemy import text

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config import settings
from app.db import engine


def _to_float(v: str | None) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return float(s)


def _to_int(v: str | None) -> int | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    return int(float(s))


def main() -> None:
    root = _ROOT
    csv_path = root / "data" / "reference_nutrition_lookup_simulated.csv"
    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    dest = settings.reference_catalog_qualified_sql

    create_sql = f"""
    CREATE TABLE IF NOT EXISTS {dest} (
        product_name TEXT,
        energy_kcal DOUBLE PRECISION NULL,
        energy_kj DOUBLE PRECISION NULL,
        protein_g DOUBLE PRECISION NULL,
        carbohydrates_g DOUBLE PRECISION NULL,
        total_sugar_g DOUBLE PRECISION NULL,
        total_fat_g DOUBLE PRECISION NULL,
        fibre_g DOUBLE PRECISION NULL,
        sodium_g DOUBLE PRECISION NULL,
        sub_type TEXT NULL,
        form TEXT NULL,
        class_name TEXT NULL,
        subclass_name TEXT NULL,
        nova TEXT NULL,
        classification_method TEXT NULL,
        class_confidence DOUBLE PRECISION NULL,
        subclass_confidence DOUBLE PRECISION NULL,
        nova_confidence DOUBLE PRECISION NULL,
        needs_review BOOLEAN NULL,
        classification_timestamp TIMESTAMPTZ NULL,
        octagon_count INTEGER NULL
    );
    """

    insert_sql = text(
        f"""
        INSERT INTO {dest} (
            product_name, energy_kcal, energy_kj, protein_g, carbohydrates_g,
            total_sugar_g, total_fat_g, fibre_g, sodium_g, sub_type, form,
            class_name, subclass_name, nova, classification_method,
            class_confidence, subclass_confidence, nova_confidence, needs_review,
            classification_timestamp, octagon_count
        ) VALUES (
            :product_name, :energy_kcal, :energy_kj, :protein_g, :carbohydrates_g,
            :total_sugar_g, :total_fat_g, :fibre_g, :sodium_g, :sub_type, :form,
            :class_name, :subclass_name, :nova, :classification_method,
            :class_confidence, :subclass_confidence, :nova_confidence, :needs_review,
            :classification_timestamp, :octagon_count
        )
        """
    )

    rows: list[dict] = []
    with csv_path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(
                {
                    "product_name": r.get("product_name") or None,
                    "energy_kcal": _to_float(r.get("energy_kcal")),
                    "energy_kj": _to_float(r.get("energy_kj")),
                    "protein_g": _to_float(r.get("protein_g")),
                    "carbohydrates_g": _to_float(r.get("carbohydrates_g")),
                    "total_sugar_g": _to_float(r.get("total_sugar_g")),
                    "total_fat_g": _to_float(r.get("total_fat_g")),
                    "fibre_g": _to_float(r.get("fibre_g")),
                    "sodium_g": _to_float(r.get("sodium_g")),
                    "sub_type": (r.get("sub_type") or None),
                    "form": (r.get("form") or None),
                    "class_name": (r.get("class_name") or None),
                    "subclass_name": (r.get("subclass_name") or None),
                    "nova": (r.get("nova") or None),
                    "classification_method": (r.get("classification_method") or None),
                    "class_confidence": _to_float(r.get("class_confidence")),
                    "subclass_confidence": _to_float(r.get("subclass_confidence")),
                    "nova_confidence": _to_float(r.get("nova_confidence")),
                    "needs_review": (r.get("needs_review") or "").strip().lower() == "true",
                    "classification_timestamp": (r.get("classification_timestamp") or None),
                    "octagon_count": _to_int(r.get("octagon_count")),
                }
            )

    with engine.begin() as conn:
        conn.execute(text(create_sql))
        conn.execute(text(f"TRUNCATE TABLE {dest}"))
        conn.execute(insert_sql, rows)
        loaded = conn.execute(text(f"SELECT COUNT(*) FROM {dest}")).scalar_one()

    print(f"Loaded rows into {dest}: {loaded}")


if __name__ == "__main__":
    main()
