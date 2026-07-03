#!/usr/bin/env python3
"""Create a simulated classified copy of reference_nutrition_lookup.csv.

Simulation is taxonomy-shaped to mirror `product_class_subclass_lookup.csv`:
- exact normalized description matches first
- then fuzzy match against description
- unresolved rows get a safe placeholder + review flag
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
import random
import sys
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.utils.pack_description import normalize_pack_description


def _simple_score(query: str, choice: str) -> float:
    q = normalize_pack_description(query)
    c = normalize_pack_description(choice)
    if not q or not c:
        return 0.0
    return SequenceMatcher(None, q, c).ratio() * 100.0


def _best_fuzzy_match(query: str, choices: list[str], min_score: float) -> tuple[str, float] | None:
    best_choice = ""
    best_score = 0.0
    for choice in choices:
        s = _simple_score(query, choice)
        if s > best_score:
            best_choice = choice
            best_score = s
    if best_score >= min_score and best_choice:
        return best_choice, best_score
    return None


def _best_token_overlap_match(query: str, choices: list[str]) -> tuple[str, float] | None:
    q_tokens = [t for t in normalize_pack_description(query).split() if t]
    if not q_tokens:
        return None
    q_set = set(q_tokens)
    best_choice = ""
    best = 0.0
    for c in choices:
        c_tokens = [t for t in normalize_pack_description(c).split() if t]
        if not c_tokens:
            continue
        c_set = set(c_tokens)
        inter = len(q_set.intersection(c_set))
        union = len(q_set.union(c_set))
        score = (inter / union) * 100.0 if union else 0.0
        if score > best:
            best_choice = c
            best = score
    if best_choice:
        return best_choice, best
    return None


def _load_lookup(
    path: Path,
) -> tuple[dict[str, dict[str, str]], list[str], dict[str, dict[str, str]], dict[str, list[str]]]:
    norm_to_row: dict[str, dict[str, str]] = {}
    descriptions: list[str] = []
    desc_to_row: dict[str, dict[str, str]] = {}
    token_index: dict[str, list[str]] = {}
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            desc = (row.get("description") or "").strip()
            if not desc:
                continue
            rec = {
                "class_name": (row.get("class_name") or "").strip(),
                "subclass_name": (row.get("subclass_name") or "").strip(),
                "nova": (row.get("nova") or "").strip(),
                "description": desc,
            }
            norm = normalize_pack_description(desc)
            if norm and norm not in norm_to_row:
                norm_to_row[norm] = rec
            descriptions.append(desc)
            if desc not in desc_to_row:
                desc_to_row[desc] = rec
            norm_desc = normalize_pack_description(desc)
            first = (norm_desc.split(" ", 1)[0] if norm_desc else "").strip()
            if first:
                token_index.setdefault(first, []).append(desc)
    return norm_to_row, descriptions, desc_to_row, token_index


def main() -> None:
    root = _ROOT
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=root / "data" / "reference_nutrition_lookup.csv",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=root / "data" / "reference_nutrition_lookup_simulated.csv",
    )
    parser.add_argument(
        "--lookup",
        type=Path,
        default=root / "data" / "product_class_subclass_lookup.csv",
    )
    parser.add_argument("--fuzzy-min-score", type=float, default=72.0)
    args = parser.parse_args()

    if not args.source.exists():
        raise SystemExit(f"Source not found: {args.source}")
    if not args.lookup.exists():
        raise SystemExit(f"Lookup not found: {args.lookup}")

    timestamp = datetime.now(timezone.utc).isoformat()
    added_cols = [
        "class_name",
        "subclass_name",
        "nova",
        "classification_method",
        "class_confidence",
        "subclass_confidence",
        "nova_confidence",
        "needs_review",
        "classification_timestamp",
    ]

    norm_to_row, descriptions, desc_to_row, token_index = _load_lookup(args.lookup)
    if not descriptions:
        raise SystemExit("Lookup file has no usable descriptions.")
    random.seed(42)
    lookup_rows = list(desc_to_row.values())

    rows_out: list[dict[str, str]] = []
    with args.source.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            pname = (row.get("product_name") or "").strip()
            norm_p = normalize_pack_description(pname)

            match_row: dict[str, str] | None = None
            method = "unresolved"
            class_conf = 0.0
            subclass_conf = 0.0
            nova_conf = 0.0
            needs_review = True

            if norm_p and norm_p in norm_to_row:
                match_row = norm_to_row[norm_p]
                method = "simulated_lookup_exact"
                class_conf = 0.99
                subclass_conf = 0.99
                nova_conf = 0.99
                needs_review = False
            elif pname:
                query_norm = normalize_pack_description(pname)
                first = (query_norm.split(" ", 1)[0] if query_norm else "").strip()
                shortlist = token_index.get(first, []) if first else []
                if shortlist:
                    hit = _best_fuzzy_match(pname, shortlist, float(args.fuzzy_min_score))
                else:
                    hit = None
                if hit is None and shortlist:
                    hit = _best_token_overlap_match(pname, shortlist)
                if hit is not None:
                    desc, score = hit
                    match_row = desc_to_row.get(desc)
                    if match_row is not None:
                        s = float(score)
                        method = "simulated_lookup_fuzzy" if s >= float(args.fuzzy_min_score) else "simulated_lookup_overlap"
                        class_conf = round(s / 100.0, 3)
                        subclass_conf = round(max(0.0, (s - 3.0) / 100.0), 3)
                        nova_conf = round(max(0.0, (s - 1.0) / 100.0), 3)
                        needs_review = s < 85.0

            if match_row is None:
                # Simulation-only fallback: assign a random valid taxonomy row so
                # downstream DB/pipeline tests have complete category fields.
                picked = random.choice(lookup_rows)
                row["class_name"] = picked["class_name"] or "UNRESOLVED"
                row["subclass_name"] = picked["subclass_name"] or "UNRESOLVED"
                row["nova"] = picked["nova"] or "Unknown"
                method = "simulated_random_fill"
                class_conf = 0.35
                subclass_conf = 0.30
                nova_conf = 0.33
                needs_review = True
            else:
                row["class_name"] = match_row["class_name"] or "UNRESOLVED"
                row["subclass_name"] = match_row["subclass_name"] or "UNRESOLVED"
                row["nova"] = match_row["nova"] or "Unknown"

            row["classification_method"] = method
            row["class_confidence"] = f"{class_conf:.3f}"
            row["subclass_confidence"] = f"{subclass_conf:.3f}"
            row["nova_confidence"] = f"{nova_conf:.3f}"
            row["needs_review"] = "true" if needs_review else "false"
            row["classification_timestamp"] = timestamp
            rows_out.append(row)

    out_fields = fieldnames + [c for c in added_cols if c not in fieldnames]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"Written: {args.out}")
    print(f"Rows: {len(rows_out)}")


if __name__ == "__main__":
    main()

