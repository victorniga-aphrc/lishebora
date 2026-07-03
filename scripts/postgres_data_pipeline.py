#!/usr/bin/env python3
"""Single entry point: clean and stage data_database CSVs for PostgreSQL load.

Stages under data_database/staged/:
  - all_categories_nutrients.csv — Food Name (cleaned), Sugar, Fat, Sodium; **deduped** by product
    name (first row kept; key is case-insensitive match key)
  - huge_data_cleaned.csv — full rows with cleaned ``description``; **deduped** by description
    (first row kept)
  - all_categories_nutrients_classified.csv — after join (exact + fuzzy) and OpenAI fallback.
    Adds:
      * ``classification_source``  : ``huge_data_exact`` | ``huge_data_fuzzy`` | ``openai`` | ``bilstm`` | ``manual`` | ""
      * ``classification_confidence``: integer 1-5 (5 = certain, 1 = guess; "" if unknown)
      * ``needs_review``           : "true" / "false" / "" (true when confidence < threshold)
    BiLSTM step is still available via ``--step bilstm_classify`` but no longer runs by default.
  - openai_review.csv (via ``--step export_review``) — suspect rows for manual review with extra
    empty columns ``corrected_class_name`` / ``corrected_subclass_name`` / ``corrected_nova`` /
    ``reviewer_note`` for hand-correction.
  - food_reference_nutrients.csv (via ``--step clean_reference_nutrition``) — secondary lookup
    table cleaned from ``food_with_all_nutrients.csv``. Same schema as
    ``all_categories_nutrients.csv`` (``Food Name, Sugar, Fat, Sodium``) so it can flow through
    the same join + OpenAI classification steps. ``Sugar`` is empty (the reference table has
    no sugar column); ``Fat`` is in g (e.g. ``1.5g``) and ``Sodium`` is in mg (e.g. ``120mg``).
  - food_reference_nutrients_classified.csv (via ``--step classify_reference_nutrition``) —
    same reference table after the SKU classification flow (huge_data join + OpenAI).

Common workflows:
  - Full SKU pipeline with default model: ``--step all``
  - Full SKU pipeline with stronger model: ``--step all --openai-model gpt-4o``
  - Re-run only OpenAI on existing rows:  ``--step openai_classify --openai-model gpt-4o``
  - Build the review CSV:                 ``--step export_review``
  - Build the secondary reference table:  ``--step reference_all``  (clean + classify)
  - Just clean the reference table:       ``--step clean_reference_nutrition``
  - Just classify the reference table:    ``--step classify_reference_nutrition``

Use ``--step prepare_clean`` to regenerate the two cleaned CSVs **without** matching, then inspect
them before ``--step join_huge_classification`` or ``--step all``.

**Interpreter:** If ``.venv`` was created under WSL/Linux (``pyvenv.cfg`` points at a Linux base
interpreter), run this script with that environment — e.g. activate ``.venv`` in WSL and use
``python scripts/postgres_data_pipeline.py``. Native Windows ``py`` may be a different Python
without TensorFlow; BiLSTM backfill will skip there.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

try:
    from rapidfuzz import fuzz as _rf_fuzz  # type: ignore[import-not-found]
    from rapidfuzz import process as _rf_process  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional until rapidfuzz installs (e.g. Py 3.14 wheels)
    _rf_fuzz = None
    _rf_process = None

_ROOT = Path(__file__).resolve().parent.parent

ALL_CATEGORIES_COLUMNS = ("Food Name", "Sugar", "Fat", "Sodium")
CLASSIFICATION_COLUMNS = ("class_name", "subclass_name", "nova")
CLASSIFICATION_SOURCE_COLUMN = "classification_source"
CLASSIFICATION_CONFIDENCE_COLUMN = "classification_confidence"
NEEDS_REVIEW_COLUMN = "needs_review"
# classification_source values: huge_data_exact | huge_data_fuzzy | openai | bilstm | (empty)
# classification_confidence: integer 1-5 (5 = certain, 1 = guess; empty when unknown)
# needs_review: "true" / "false" / "" (true when confidence below threshold or low signal)
NUTRIENTS_WITH_CLASS_FIELDS = (
    *ALL_CATEGORIES_COLUMNS,
    *CLASSIFICATION_COLUMNS,
    CLASSIFICATION_SOURCE_COLUMN,
    CLASSIFICATION_CONFIDENCE_COLUMN,
    NEEDS_REVIEW_COLUMN,
)
CLASSIFIED_REQUIRED_READ_COLUMNS = (*ALL_CATEGORIES_COLUMNS, *CLASSIFICATION_COLUMNS)
DEFAULT_REVIEW_CONFIDENCE_THRESHOLD = 4
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"

# Secondary food-composition reference table (food_with_all_nutrients.csv).
# After cleaning we keep ONLY the columns that match all_categories_nutrients.csv so the same
# join + classification steps work unchanged. The reference file has no sugar column, so the
# Sugar column is always empty in the cleaned reference.
FOOD_REFERENCE_SOURCE_FILE = "food_with_all_nutrients.csv"
FOOD_REFERENCE_CLEANED_FILE = "food_reference_nutrients.csv"
FOOD_REFERENCE_CLASSIFIED_FILE = "food_reference_nutrients_classified.csv"
FOOD_REFERENCE_CORRECTIONS_FILE = "food_reference_corrections.csv"


def _confidence_to_str(conf: int | float | None) -> str:
    if conf is None or conf == "":
        return ""
    try:
        return str(int(conf))
    except (TypeError, ValueError):
        return ""


def _needs_review_str(needs: bool) -> str:
    return "true" if needs else "false"

# Mass / volume unit (no leading \s; ends with word boundary).
_UNIT = r"(?:g|gm|gr|grams?|kg|kgs?|mg|ml|mils?|ltrs?|ltre?|lts?|lt|cl|l)\b"

# Trailing pack / count segments (applied repeatedly from the right, before
# replacing "." so decimals like 16.70g stay intact).
_TRAILING_PAREN_PACK = re.compile(
    r"(?i)\s*\(\s*\d[^)]{0,199}(?:pieces?|pack|pkts?|pcks?|pcs?|"
    r"\d+\s*(?:g|kg|ml)\b|\d+(?:\.\d+)?\s*(?:g|kg|ml)\b)[^)]{0,79}\)\s*$",
)
_TRAILING_SPACE_COUNT_UNIT = re.compile(
    r"(?i)\s+\d+(?:\.\d+)?\s*(?:pieces?|packs?|pkts?|pcks?|pcs?|pc|sachets?)\s*$",
)
_TRAILING_SPACE_NUMBER_UNIT = re.compile(
    rf"(?i)\s+\d+(?:\.\d+)?\s*{_UNIT}\s*$",
)
_TRAILING_GLUED_NUMBER_UNIT = re.compile(
    r"(?i)(?<=[^\d\s])\d+(?:\.\d+)?"
    r"(?:g|gm|gr|kg|kgs?|mg|ml|ltrs?|ltr|lts?|lt|pcs?|pkts?|pkt|pc|pack)\s*$",
)
# POS / packaging tails (after volumes stripped or alongside)
_TRAILING_POS_PACKAGING = re.compile(
    r"(?i)\s+(?:pl|pet|tr)\s+(?:btl|bottle)\s*$"
    r"|\s+pln\s*$"
    r"|\s+pl\s*$"
    r"|\s+pet\s*$"
    r"|\s+tr\s*$"
    r"|\s+cup\s*$"
    r"|\s+(?:pkt|pkts?|pcks?)\s*$"
    r"|\s+(?:btl|bottle)\s*$"
    r"|\s+j\s+super\s*$"
    r"|\s+super\s*$"
    r"|\s+m\s+b\s*$"
    r"|\s+v\s*pack\s*$"
    r"|\s+(?:box|boxes|jar|jars)\s*$"
    r"|\s+pack\s*$",
)
# Promo / multi-buy / count glued (remove anywhere)
_RE_BXG = re.compile(r"(?i)\bB\d+G\d+\b")
_RE_N_IN_N = re.compile(r"(?i)\b\d+\s*IN\s*\d+\b")
_RE_NINN = re.compile(r"(?i)\b\d+IN\d+\b")
_RE_D_IN_D = re.compile(r"(?i)\b\d+in\d+\b")  # 2In1, 3In1
_RE_G_STAR_N = re.compile(r"(?i)\d+(?:\.\d+)?\s*g\s*\*\s*\d+")
_RE_STAR_G = re.compile(r"(?i)\d+\s*\*\s*\d+g")
_RE_GX_N = re.compile(r"(?i)\d+(?:\.\d+)?GX\d+")  # 18.5GX10
_RE_TRAIL_DECIMAL_XN = re.compile(r"(?i)\d+(?:\.\d+)?X\d+\s*$")  # 18.5X10 sachet counts
_RE_PERCENT = re.compile(r"(?i)\b\d+(?:\.\d+)?%")  # no \b after % (% is non-word)
_RE_PACK_OF_PHRASE = re.compile(
    rf"(?i)\d+(?:\.\d+)?\s*(?:{_UNIT})?\s*x\s*pack\s*of\s*\d+",
)
_RE_TRAIL_GX_ONLY = re.compile(rf"(?i)\d+(?:\.\d+)?\s*{_UNIT}\s+x\s*$")
_RE_TRAIL_STAR_GLOSS = re.compile(r"(?i)\s+\d+\s*\*\s*\d+g\s+sats\b")
_RE_1_SPACE_5_VOLUME = re.compile(
    r"(?i)\b1\s+5\s*(l(?:tr|trs?)?|ml)\b",
)  # "1 5LTR" mis-scan → 1.5L
_RE_MIDDLE_QTY_TOKEN = re.compile(
    rf"(?i)\s+\d+(?:\.\d+)?{_UNIT}(?=\s|$)",
)
_RE_SHORT_KG = re.compile(r"(?i)\s+\d+k\b(?=\s|$)")  # "1K" shorthand
_RE_SACHET_COUNT = re.compile(r"(?i)\s+\d+\s+sachets?\b")
_RE_MULTI_PACK_TAIL = re.compile(
    r"(?i)\s+\d+\s*pack\s+\d+g\s+.*$",
)  # "8 PACK 400G J SUPER"


def _collapse_ws(s: str) -> str:
    return re.sub(r"[\s\xa0\u2000-\u200b\ufeff]+", " ", s).strip()


def _punct_to_spaces(s: str) -> str:
    """Map punctuation to spaces without splitting digit.decimal digit (e.g. ``18.5``)."""
    s = re.sub(r"[,;]+", " ", s)
    s = re.sub(r"[/\\|]+", " ", s)
    s = re.sub(r"(?<![0-9])\.(?![0-9])", " ", s)
    s = re.sub(r"\.{2,}", " ", s)
    s = re.sub(r"(?<![0-9])_(?![0-9])", " ", s)
    s = re.sub(r"_+", " ", s)
    s = re.sub(r"[-–—]+", " ", s)
    s = re.sub(r"[&+]+", " ", s)
    s = re.sub(r"[''`´]+", " ", s)
    return s


def _strip_trailing_pack_loop(s: str) -> str:
    patterns = (
        _TRAILING_PAREN_PACK,
        _TRAILING_SPACE_COUNT_UNIT,
        _TRAILING_SPACE_NUMBER_UNIT,
        _TRAILING_GLUED_NUMBER_UNIT,
        _TRAILING_POS_PACKAGING,
        _RE_TRAIL_GX_ONLY,
        _RE_TRAIL_STAR_GLOSS,
        _RE_TRAIL_DECIMAL_XN,
        _RE_MULTI_PACK_TAIL,
    )
    changed = True
    guard = 0
    while changed and guard < 32:
        guard += 1
        changed = False
        for pat in patterns:
            new = pat.sub("", s)
            if new != s:
                s = new.strip()
                changed = True
                break
    return s


def _remove_middle_qty_tokens(s: str) -> str:
    """Remove standalone mass/volume tokens (e.g. ``2KG`` in ``SUGAR 2KG BROWN``)."""
    prev = None
    guard = 0
    while prev != s and guard < 24:
        prev = s
        guard += 1
        s = _RE_MIDDLE_QTY_TOKEN.sub(" ", s)
        s = _collapse_ws(s)
    return s


def normalize_product_display(raw: str) -> str:
    """Clean a product label for storage/display (preserves letter casing).

    NFKC; remove promos (``B2G1``, ``5IN1`` / ``5 IN 1``, ``70G*5``, ``400*3G``,
    ``18.5GX10`` / ``18.5X10``), ``… x Pack of N``, percentage claims like ``100%``,
    middle tokens like ``2KG`` in ``SUGAR 2KG BROWN``, ``1K``-style kg shorthand,
    ``9 Sachet`` counts; strip
    POS tails (``PL BTL``, ``PLN``, ``CUP``, ``PET``, ``M B``); normalize ``/`` and
    other punctuation to spaces without splitting digit decimals (``18.5``). Join
    keys use :func:`normalize_product_match_key` (this string, then casefold).
    """
    s = unicodedata.normalize("NFKC", (raw or "").strip())
    if not s:
        return ""

    s = _RE_1_SPACE_5_VOLUME.sub("1.5\\1", s)

    for _ in range(4):
        s = _RE_BXG.sub(" ", s)
        s = _RE_N_IN_N.sub(" ", s)
        s = _RE_NINN.sub(" ", s)
        s = _RE_D_IN_D.sub(" ", s)
        s = _RE_G_STAR_N.sub(" ", s)
        s = _RE_STAR_G.sub(" ", s)
        s = _RE_GX_N.sub(" ", s)
        s = _RE_PERCENT.sub(" ", s)
        s = _RE_PACK_OF_PHRASE.sub(" ", s)
        s = _collapse_ws(s)

    s = _strip_trailing_pack_loop(s)
    s = _remove_middle_qty_tokens(s)
    s = _RE_SHORT_KG.sub(" ", s)
    s = _RE_SACHET_COUNT.sub(" ", s)
    s = _collapse_ws(s)
    s = _strip_trailing_pack_loop(s)

    s = _punct_to_spaces(s)
    s = re.sub(r"\*+", " ", s)
    s = _collapse_ws(s)

    s = _strip_trailing_pack_loop(s)
    s = _remove_middle_qty_tokens(s)
    s = _collapse_ws(s)

    return s


def normalize_product_match_key(raw: str) -> str:
    """Case-insensitive key for matching (display-normalize then casefold)."""
    return normalize_product_display(raw).casefold()


_warned_rapidfuzz_fallback = False


def _fuzzy_best_match_idx(
    query: str,
    choices: list[str],
    cutoff: float,
) -> tuple[int, float] | None:
    """Best match index and score in 0-100, or None if below ``cutoff``."""
    global _warned_rapidfuzz_fallback
    if not (query or "").strip() or not choices:
        return None
    if _rf_process is not None and _rf_fuzz is not None:
        hit = _rf_process.extractOne(
            query,
            choices,
            scorer=_rf_fuzz.WRatio,
            score_cutoff=float(cutoff),
        )
        if hit is None:
            return None
        _s, score, idx = hit
        return int(idx), float(score)

    if not _warned_rapidfuzz_fallback:
        print(
            "Note: rapidfuzz is not installed; fuzzy step uses difflib on a token-overlap "
            "shortlist (not identical to WRatio). Install rapidfuzz for the intended scorer.",
            file=sys.stderr,
        )
        _warned_rapidfuzz_fallback = True

    from difflib import SequenceMatcher

    q = query.casefold()
    qtok = set(q.split())
    ranked: list[tuple[int, float]] = []
    for i, c in enumerate(choices):
        ctok = set(c.casefold().split())
        if qtok and ctok:
            jac = len(qtok & ctok) / len(qtok | ctok)
        elif qtok or ctok:
            jac = 0.0
        else:
            jac = 1.0
        ranked.append((i, jac))
    ranked.sort(key=lambda x: -x[1])
    shortlist = min(50, len(ranked))
    best_idx = -1
    best_score = -1.0
    for i, _jac in ranked[:shortlist]:
        sc = SequenceMatcher(None, q, choices[i].casefold()).ratio() * 100.0
        if sc > best_score:
            best_score = sc
            best_idx = i
    if best_idx >= 0 and best_score >= float(cutoff):
        return best_idx, best_score
    return None


def step_all_categories_nutrient_columns(
    source: Path,
    dest: Path,
) -> None:
    """Keep only Food Name, Sugar, Fat, Sodium from all_categories_combined.

    All three nutrient values are normalised to grams (``"<num>g"``); source values in
    ``mg`` / ``kg`` / ``mcg`` (and typos like ``gm`` / ``mmg`` / ``ng``) are converted.
    Empty / placeholder cells stay empty.
    """
    if not source.is_file():
        raise FileNotFoundError(f"Source CSV not found: {source}")

    dest.parent.mkdir(parents=True, exist_ok=True)

    with source.open(newline="", encoding="utf-8-sig", errors="replace") as f_in:
        reader = csv.DictReader(f_in)
        if reader.fieldnames is None:
            raise ValueError(f"No header row in {source}")

        missing = [c for c in ALL_CATEGORIES_COLUMNS if c not in reader.fieldnames]
        if missing:
            raise ValueError(
                f"{source} is missing required columns {missing!r}; "
                f"found {list(reader.fieldnames)!r}"
            )

        seen_names: set[str] = set()
        dup_rows = 0
        out_rows = 0
        buffer: list[dict[str, str]] = []
        for row in reader:
            out = {k: (row.get(k) or "").strip() for k in ALL_CATEGORIES_COLUMNS}
            out["Food Name"] = normalize_product_display(out["Food Name"])
            for col in ("Sugar", "Fat", "Sodium"):
                out[col] = _normalize_to_grams(out[col])
            key = normalize_product_match_key(out["Food Name"])
            if key in seen_names:
                dup_rows += 1
                continue
            seen_names.add(key)
            buffer.append(out)
            out_rows += 1

        with dest.open("w", newline="", encoding="utf-8", errors="replace") as f_out:
            writer = csv.DictWriter(f_out, fieldnames=list(ALL_CATEGORIES_COLUMNS))
            writer.writeheader()
            writer.writerows(buffer)

    print(
        f"Wrote {dest} ({out_rows} rows, {dup_rows} duplicate product names skipped; "
        f"{', '.join(ALL_CATEGORIES_COLUMNS)})",
    )


def step_clean_huge_data_descriptions(source: Path, dest: Path) -> None:
    """Write a copy of huge_data with ``description`` permanently cleaned (same rules as Food Name)."""
    if not source.is_file():
        raise FileNotFoundError(f"huge_data CSV not found: {source}")

    dest.parent.mkdir(parents=True, exist_ok=True)
    rows_written = 0
    dup_rows = 0

    with source.open(newline="", encoding="utf-8-sig", errors="replace") as f_in:
        reader = csv.DictReader(f_in)
        if reader.fieldnames is None:
            raise ValueError(f"No header row in {source}")
        if "description" not in reader.fieldnames:
            raise ValueError(f"{source} has no 'description' column; found {list(reader.fieldnames)!r}")

        fieldnames = reader.fieldnames
        seen_desc: set[str] = set()
        buffer: list[dict[str, str]] = []
        for row in reader:
            row = dict(row)
            row["description"] = normalize_product_display((row.get("description") or "").strip())
            key = normalize_product_match_key(row["description"])
            if key in seen_desc:
                dup_rows += 1
                continue
            seen_desc.add(key)
            buffer.append(row)
            rows_written += 1

        with dest.open("w", newline="", encoding="utf-8", errors="replace") as f_out:
            writer = csv.DictWriter(f_out, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(buffer)

    print(
        f"Wrote {dest} ({rows_written} rows, {dup_rows} duplicate descriptions skipped; "
        "description column cleaned)",
    )


# --- Nutrient unit normalisation ---
# All Sugar / Fat / Sodium values are stored in grams with a trailing ``g`` suffix
# (e.g. ``"1.5g"``, ``"0.12g"``). Source CSVs use a mix of g, mg, kg, plus a few typos
# (``gm``, ``mmg``, ``ng``). Normalise to grams to keep the schema consistent.
_NUTRIENT_UNIT_TO_GRAMS: dict[str, float] = {
    "": 1.0,        # no unit -> assume grams (the source label is per 100 g)
    "g": 1.0,
    "gm": 1.0,      # typo of 'g'
    "gms": 1.0,
    "gram": 1.0,
    "grams": 1.0,
    "kg": 1000.0,
    "kgs": 1000.0,
    "mg": 0.001,
    "mgs": 0.001,
    "mmg": 0.001,   # observed typo (1 row); treat as mg
    "ng": 0.001,    # observed typo (1 row); nanograms make no sense for Na per 100 g
    "mcg": 1e-6,
    "ug": 1e-6,
    "\u00b5g": 1e-6,    # micro sign + g
    "\u03bcg": 1e-6,    # greek small mu + g
}
_NUTRIENT_QTY_RE = re.compile(r"\s*(-?\d+(?:[.,]\d+)?)\s*([A-Za-z\u00b5\u03bc]*)\s*")
_NUTRIENT_BLANK_TOKENS = {"", "-", "na", "n/a", "nan", "null", "tr", "trace", "\u2014"}


def _format_grams(grams: float) -> str:
    if grams == int(grams):
        return f"{int(grams)}g"
    formatted = f"{grams:.6f}".rstrip("0").rstrip(".")
    return f"{formatted}g"


def _normalize_to_grams(raw: str) -> str:
    """Parse a nutrient cell and return its value in grams as a ``"<num>g"`` string.

    Examples::

        _normalize_to_grams("120mg")  # -> "0.12g"
        _normalize_to_grams("1.5g")   # -> "1.5g"
        _normalize_to_grams("0")      # -> "0g"
        _normalize_to_grams("")       # -> ""
        _normalize_to_grams("trace")  # -> ""

    Unrecognised units fall back to grams (the most common SKU-label default).
    Returns ``""`` for empty / unparseable / placeholder strings.
    """
    s = (raw or "").strip()
    if not s or s.lower() in _NUTRIENT_BLANK_TOKENS:
        return ""
    s = s.replace(",", ".")
    m = _NUTRIENT_QTY_RE.fullmatch(s)
    if not m:
        return ""
    try:
        num = float(m.group(1))
    except ValueError:
        return ""
    unit = (m.group(2) or "").strip().lower()
    multiplier = _NUTRIENT_UNIT_TO_GRAMS.get(unit, 1.0)
    grams = num * multiplier
    return _format_grams(grams)


# --- Mojibake fix for the food-composition reference file ---
# The source file is UTF-8 but contains \u0093 / \u0094 (Windows-1252 smart-quote control codes
# that were incorrectly encoded as their own UTF-8 sequence). Map them to readable characters
# before further normalisation.
_REFERENCE_MOJIBAKE_MAP = {
    "\u0091": "'",
    "\u0092": "'",
    "\u0093": '"',
    "\u0094": '"',
    "\u0095": "*",
    "\u0096": "-",
    "\u0097": "-",
    "\ufffd": "",
}


def _fix_reference_mojibake(s: str) -> str:
    if not s:
        return s
    for bad, good in _REFERENCE_MOJIBAKE_MAP.items():
        if bad in s:
            s = s.replace(bad, good)
    return s


def step_clean_food_reference_nutrition(source: Path, dest: Path) -> None:
    """Clean food_with_all_nutrients.csv into the same schema as all_categories_nutrients.csv.

    Output columns: ``Food Name, Sugar, Fat, Sodium``.

    - ``Food Name``: NFKC + ``normalize_product_display`` (same rules as the SKU table).
      Mojibake codes from the source (``\\u0093``, ``\\u0094`` …) are repaired first.
    - ``Sugar``: always empty — the reference file has no sugar column.
    - ``Fat``: parsed from ``Fat(g)`` and stored in grams (``"<num>g"``).
    - ``Sodium``: parsed from ``Na(mg)`` and **converted from mg to grams** (``"<num>g"``).
      e.g. ``120 mg -> "0.12g"``.

    Rows with no Food Name are skipped. Duplicates (case-insensitive normalised name) are
    deduplicated, keeping the first occurrence — matching the behaviour of
    :func:`step_all_categories_nutrient_columns`.
    """
    if not source.is_file():
        raise FileNotFoundError(f"Reference nutrient CSV not found: {source}")

    dest.parent.mkdir(parents=True, exist_ok=True)

    with source.open(newline="", encoding="utf-8", errors="replace") as f_in:
        reader = csv.DictReader(f_in)
        if reader.fieldnames is None:
            raise ValueError(f"No header row in {source}")
        # Column names in the source can have stray trailing spaces — tolerate that.
        col_lookup: dict[str, str] = {
            (c.strip().casefold() if c else ""): c for c in reader.fieldnames
        }
        food_col = col_lookup.get("food name")
        fat_col = col_lookup.get("fat(g)")
        na_col = col_lookup.get("na(mg)")
        if not food_col or not fat_col or not na_col:
            raise ValueError(
                f"{source} is missing required columns. Looking for 'Food name', 'Fat(g)', "
                f"'Na(mg)' — found {list(reader.fieldnames)!r}",
            )

        seen_keys: set[str] = set()
        out_rows: list[dict[str, str]] = []
        skipped_empty = 0
        dup_rows = 0
        for row in reader:
            raw_name = _fix_reference_mojibake((row.get(food_col) or "").strip())
            food = normalize_product_display(raw_name)
            key = normalize_product_match_key(food)
            if not key:
                skipped_empty += 1
                continue
            if key in seen_keys:
                dup_rows += 1
                continue
            seen_keys.add(key)
            # Tag values with their source unit so the shared normaliser converts to grams.
            fat_raw = (row.get(fat_col) or "").strip()
            na_raw = (row.get(na_col) or "").strip()
            fat_input = f"{fat_raw}g" if fat_raw and not re.search(r"[A-Za-z]", fat_raw) else fat_raw
            na_input = f"{na_raw}mg" if na_raw and not re.search(r"[A-Za-z]", na_raw) else na_raw
            out_rows.append(
                {
                    "Food Name": food,
                    "Sugar": "",  # not in reference table
                    "Fat": _normalize_to_grams(fat_input),
                    "Sodium": _normalize_to_grams(na_input),
                },
            )

    tmp_path = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with tmp_path.open("w", newline="", encoding="utf-8", errors="replace") as f_out:
            writer = csv.DictWriter(f_out, fieldnames=list(ALL_CATEGORIES_COLUMNS))
            writer.writeheader()
            writer.writerows(out_rows)
        tmp_path.replace(dest)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise OSError(f"Could not write {dest}: {exc}") from exc

    print(
        f"Wrote {dest} ({len(out_rows)} rows, {dup_rows} duplicate names skipped, "
        f"{skipped_empty} rows without a Food Name skipped; "
        f"columns: {', '.join(ALL_CATEGORIES_COLUMNS)} from {source.name})",
    )


def print_clean_outputs_inspection(
    nutrients_csv: Path,
    huge_cleaned_csv: Path,
    *,
    preview_rows: int,
) -> None:
    """Print paths and sample rows so you can review cleaning before running the join."""
    print()
    print("=== Inspection (cleaning only; join not run) ===")
    print(f"Nutrients (Food Name + nutrition columns):\n  {nutrients_csv.resolve()}")
    if nutrients_csv.is_file():
        with nutrients_csv.open(newline="", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.reader(f)
            for i, row in enumerate(reader):
                if i > preview_rows:
                    break
                print(f"  {row!r}")
    else:
        print("  (file missing)")

    print()
    print(f"huge_data cleaned (description column only shown):\n  {huge_cleaned_csv.resolve()}")
    if huge_cleaned_csv.is_file():
        with huge_cleaned_csv.open(newline="", encoding="utf-8-sig", errors="replace") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                print("  (no header)")
            else:
                for i, row in enumerate(reader):
                    if i >= preview_rows:
                        break
                    desc = (row.get("description") or "").strip()
                    print(f"  {i + 1}: {desc!r}")
    else:
        print("  (file missing)")

    print()
    print(
        "When satisfied, run matching with:\n"
        f"  py -3 scripts/postgres_data_pipeline.py --step join_huge_classification\n"
        "Or run the full pipeline (clean + join):\n"
        f"  py -3 scripts/postgres_data_pipeline.py --step all",
    )


def step_join_huge_data_exact_description(
    huge_data_csv: Path,
    nutrients_in: Path,
    nutrients_out: Path,
    *,
    fuzzy_cutoff: int = 90,
    review_threshold: int = DEFAULT_REVIEW_CONFIDENCE_THRESHOLD,
) -> None:
    """Add class_name, subclass_name, nova: exact key match first, then optional fuzzy (WRatio).

    Also emits ``classification_confidence`` (5 exact / 3-5 fuzzy by score) and ``needs_review``
    (true when confidence < ``review_threshold``).

    ``fuzzy_cutoff`` is 0-100 for RapidFuzz ``WRatio``; use 0 to disable fuzzy fallback.
    """
    if not huge_data_csv.is_file():
        raise FileNotFoundError(f"huge_data CSV not found: {huge_data_csv}")
    if not nutrients_in.is_file():
        raise FileNotFoundError(f"Nutrients CSV not found: {nutrients_in}")

    required_huge = ("description", *CLASSIFICATION_COLUMNS)
    desc_to_class: dict[str, tuple[str, str, str]] = {}
    # One display string per unique normalized key (for fuzzy corpus)
    fuzzy_corpus: list[tuple[str, tuple[str, str, str]]] = []
    duplicate_descriptions = 0
    conflicting_duplicates = 0

    with huge_data_csv.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError(f"No header row in {huge_data_csv}")
        missing = [c for c in required_huge if c not in reader.fieldnames]
        if missing:
            raise ValueError(
                f"{huge_data_csv} is missing columns {missing!r}; "
                f"found {list(reader.fieldnames)!r}"
            )
        for row in reader:
            desc = (row.get("description") or "").strip()
            desc_key = normalize_product_match_key(desc)
            if not desc_key:
                continue
            meta = tuple((row.get(c) or "").strip() for c in CLASSIFICATION_COLUMNS)
            if desc_key in desc_to_class:
                duplicate_descriptions += 1
                if desc_to_class[desc_key] != meta:
                    conflicting_duplicates += 1
                continue
            desc_to_class[desc_key] = meta
            fuzzy_corpus.append((desc, meta))

    choice_strings = [t[0] for t in fuzzy_corpus]
    use_fuzzy = fuzzy_cutoff > 0 and len(choice_strings) > 0

    total_rows = 0
    matched_exact = 0
    matched_fuzzy = 0
    matched_examples: list[str] = []
    fuzzy_examples: list[tuple[str, str, float]] = []
    out_rows: list[dict[str, str]] = []

    with nutrients_in.open(newline="", encoding="utf-8-sig", errors="replace") as f_in:
        reader = csv.DictReader(f_in)
        if reader.fieldnames is None:
            raise ValueError(f"No header row in {nutrients_in}")
        missing = [c for c in ALL_CATEGORIES_COLUMNS if c not in reader.fieldnames]
        if missing:
            raise ValueError(
                f"{nutrients_in} is missing columns {missing!r}; "
                f"found {list(reader.fieldnames)!r}"
            )
        for row in reader:
            total_rows += 1
            food = (row.get("Food Name") or "").strip()
            base = {k: (row.get(k) or "").strip() for k in ALL_CATEGORIES_COLUMNS}
            food_key = normalize_product_match_key(food)
            cls = sub = nova = ""
            src = ""
            conf: int | None = None
            score_val: float | None = None

            if food_key and food_key in desc_to_class:
                matched_exact += 1
                cls, sub, nova = desc_to_class[food_key]
                src = "huge_data_exact"
                conf = 5
                if len(matched_examples) < 8:
                    matched_examples.append(food)
            elif use_fuzzy and food:
                fuzzy_hit = _fuzzy_best_match_idx(food, choice_strings, float(fuzzy_cutoff))
                if fuzzy_hit is not None:
                    idx, score = fuzzy_hit
                    matched_fuzzy += 1
                    matched_desc = choice_strings[idx]
                    cls, sub, nova = fuzzy_corpus[idx][1]
                    src = "huge_data_fuzzy"
                    score_val = float(score)
                    if score_val >= 98:
                        conf = 5
                    elif score_val >= 95:
                        conf = 4
                    else:
                        conf = 3
                    if len(fuzzy_examples) < 6:
                        fuzzy_examples.append((food, matched_desc, score_val))

            needs_review = bool(src) and (conf is None or conf < review_threshold)
            base.update(
                {
                    "class_name": cls,
                    "subclass_name": sub,
                    "nova": nova,
                    CLASSIFICATION_SOURCE_COLUMN: src,
                    CLASSIFICATION_CONFIDENCE_COLUMN: _confidence_to_str(conf),
                    NEEDS_REVIEW_COLUMN: _needs_review_str(needs_review) if src else "",
                },
            )
            out_rows.append(base)

    nutrients_out.parent.mkdir(parents=True, exist_ok=True)
    with nutrients_out.open("w", newline="", encoding="utf-8", errors="replace") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=list(NUTRIENTS_WITH_CLASS_FIELDS))
        writer.writeheader()
        writer.writerows(out_rows)

    matched_total = matched_exact + matched_fuzzy
    unmatched = total_rows - matched_total
    pct = (100.0 * matched_total / total_rows) if total_rows else 0.0
    print(
        f"Joined {huge_data_csv.name}: exact {matched_exact}, fuzzy (>={fuzzy_cutoff}%) "
        f"{matched_fuzzy}, total {matched_total}/{total_rows} ({pct:.2f}%), unmatched {unmatched}. "
        f"Unique description keys: {len(desc_to_class)}.",
    )
    if matched_examples:
        print(f"  Example exact Food Name(s): {matched_examples!r}")
    if fuzzy_examples:
        print("  Example fuzzy matches (Food Name -> huge description, score):")
        for fn, hd, sc in fuzzy_examples:
            print(f"    {fn!r} -> {hd!r} ({sc:.1f})")
    if duplicate_descriptions:
        print(
            f"  huge_data: {duplicate_descriptions} extra rows reused an existing description; "
            f"{conflicting_duplicates} had differing class/subclass/nova (first row kept).",
        )
    print(f"Wrote {nutrients_out}")


def _load_huge_label_universe(
    huge_data_csv: Path,
) -> tuple[list[tuple[str, str, str]], list[str], list[str], list[str]]:
    """Read the cleaned huge_data file and return label universe used for OpenAI prompts.

    Returns:
        (triples, classes, subclasses, novas)
        - triples: unique (class_name, subclass_name, nova) seen in huge_data
        - classes / subclasses / novas: sorted unique single-field values
    """
    if not huge_data_csv.is_file():
        raise FileNotFoundError(f"huge_data CSV not found: {huge_data_csv}")
    triples: set[tuple[str, str, str]] = set()
    classes: set[str] = set()
    subclasses: set[str] = set()
    novas: set[str] = set()
    with huge_data_csv.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            cls = (row.get("class_name") or "").strip()
            sub = (row.get("subclass_name") or "").strip()
            nova = (row.get("nova") or "").strip()
            if not (cls or sub or nova):
                continue
            if cls:
                classes.add(cls)
            if sub:
                subclasses.add(sub)
            if nova:
                novas.add(nova)
            triples.add((cls, sub, nova))
    return (
        sorted(triples),
        sorted(classes),
        sorted(subclasses),
        sorted(novas),
    )


_OPENAI_SYSTEM_PROMPT = (
    "You classify Kenyan supermarket food/beverage products into a FIXED taxonomy.\n"
    "You MUST pick values strictly from the provided allow-lists, or return null when no listed value fits.\n"
    "Output strict JSON only (no prose).\n\n"
    "Disambiguation rules (apply in order, the actual food noun ALWAYS wins over brand words):\n"
    "1. Identify the most specific FOOD NOUN in the product name (e.g. 'flour', 'biscuit', 'essence', "
    "'sauce', 'rice'). Brand words may be misleading and must NEVER override the actual food noun.\n"
    "2. 'CARRS TABLE WATER' is a brand of CRACKERS/BISCUITS, not water. Tokens like 'BITE', 'BITES', "
    "'BISCUIT', 'BISCUITS', 'CRACKER', 'CRACKERS', 'COOKIE', 'COOKIES', 'WAFER', 'WAFERS' indicate "
    "baked snacks even when the product or brand contains the word 'WATER'.\n"
    "3. 'ESSENCE', 'EXTRACT', 'FLAVOUR', 'FLAVOR', 'FLAVOURING', 'FLAVORING' = a flavour concentrate "
    "(e.g. 'Vanilla Essence', 'Chocolate Essence'). These are NOT 'baking powder' or 'baking soda'. "
    "Pick a flavouring/essence/baking-additive subclass ONLY if the allow-list contains an explicit "
    "essence/flavour option; otherwise return null for the affected fields.\n"
    "4. 'FLOUR' (including 'self-raising flour', 'self raising flour', 'all-purpose flour', "
    "'whole wheat flour', 'maida', 'atta') = a FLOUR. Self-raising flour is still flour, NOT 'baking "
    "powder'. Map to a flour class/subclass.\n"
    "5. 'BAKING POWDER', 'BAKING SODA', 'BICARBONATE OF SODA', 'YEAST' = leavening agents only. Do NOT "
    "use these labels for flour, essence, sugar, salt, or any non-leavening item.\n"
    "6. 'MINERAL WATER', 'DRINKING WATER', 'BOTTLED WATER', 'STILL WATER', 'SPARKLING WATER' = drinking "
    "water; ONLY if the product is actually water (see rule 2).\n"
    "7. 'JUICE', 'SODA', 'COLA', 'CORDIAL', 'SQUASH' = non-alcoholic beverages; not water.\n"
    "8. 'OIL' (cooking oil, sunflower oil, olive oil) = edible oil; not 'fat spread' or 'margarine'.\n"
    "9. 'MILK' = dairy milk; flavoured milk drinks are still milk-based beverages, not water/soda.\n"
    "10. 'TEA', 'COFFEE' = the tea/coffee category; 'COFFEE BREAK CAPU' is instant coffee, not biscuit.\n"
    "11. If you are not at least somewhat confident OR the allow-lists do not contain a suitable label, "
    "set the affected fields to null and lower the confidence score.\n\n"
    "Confidence (1-5):\n"
    "  5 = definitely correct (clear food noun match, no conflicting cues)\n"
    "  4 = very likely (one strong signal, no conflicts)\n"
    "  3 = leaning correct (some ambiguity)\n"
    "  2 = guess (weak signals, ambiguous brand)\n"
    "  1 = essentially unknown / forced choice\n"
    "Be honest. Low confidence is preferred over a wrong high-confidence answer."
)


def _openai_classify_one(
    *,
    client: object,
    model: str,
    food_name: str,
    classes: list[str],
    subclasses: list[str],
    novas: list[str],
) -> tuple[str, str, str, int, str] | None:
    """Ask the chat model to pick (class_name, subclass_name, nova, confidence 1-5, reason).

    Returns ``None`` if the model declines or output cannot be parsed.
    """
    import json

    system = _OPENAI_SYSTEM_PROMPT
    user = (
        f"Product name: {food_name!r}\n\n"
        "Allowed class_name values:\n"
        f"{json.dumps(classes, ensure_ascii=False)}\n\n"
        "Allowed subclass_name values:\n"
        f"{json.dumps(subclasses, ensure_ascii=False)}\n\n"
        "Allowed nova values:\n"
        f"{json.dumps(novas, ensure_ascii=False)}\n\n"
        "Apply the disambiguation rules above. Output JSON ONLY with this exact schema:\n"
        "{\n"
        '  "class_name": "<one of class_name allow-list, or null>",\n'
        '  "subclass_name": "<one of subclass_name allow-list, or null>",\n'
        '  "nova": "<one of nova allow-list, or null>",\n'
        '  "confidence": <integer 1 to 5>,\n'
        '  "reason": "<<= 120 chars, the food noun you used>"\n'
        "}"
    )

    try:
        r = client.chat.completions.create(  # type: ignore[attr-defined]
            model=model,
            temperature=0.0,
            max_tokens=300,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:  # pragma: no cover - network/runtime errors
        print(f"  OpenAI call failed for {food_name!r}: {exc}", file=sys.stderr)
        return None

    text = (r.choices[0].message.content or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    cls = data.get("class_name") or ""
    sub = data.get("subclass_name") or ""
    nova = data.get("nova") or ""
    cls_s = str(cls).strip() if cls else ""
    sub_s = str(sub).strip() if sub else ""
    nova_s = str(nova).strip() if nova else ""

    if cls_s and cls_s not in set(classes):
        cls_s = ""
    if sub_s and sub_s not in set(subclasses):
        sub_s = ""
    if nova_s and nova_s not in set(novas):
        nova_s = ""

    raw_conf = data.get("confidence")
    try:
        conf_int = int(raw_conf) if raw_conf is not None else 1
    except (TypeError, ValueError):
        conf_int = 1
    conf_int = max(1, min(5, conf_int))

    reason = str(data.get("reason") or "").strip()
    if len(reason) > 200:
        reason = reason[:200]

    if not (cls_s or sub_s or nova_s):
        return None
    return cls_s, sub_s, nova_s, conf_int, reason


def step_openai_fill_missing_classification(
    classified_csv: Path,
    huge_data_csv: Path,
    *,
    workers: int = 8,
    model_override: str | None = None,
    review_threshold: int = DEFAULT_REVIEW_CONFIDENCE_THRESHOLD,
) -> None:
    """Fill empty class_name / subclass_name / nova rows using OpenAI structured classification.

    Labels are restricted to the union seen in ``huge_data_cleaned.csv``. Sets
    ``classification_source = openai``, ``classification_confidence`` (1-5 from the model),
    and ``needs_review`` (true when confidence < ``review_threshold``) for filled rows.

    ``model_override`` takes precedence over ``settings.openai_model`` / ``OPENAI_MODEL``.
    ``workers`` controls how many requests run in parallel via a thread pool (default 8).
    """
    if not classified_csv.is_file():
        raise FileNotFoundError(f"Classified CSV not found: {classified_csv}")

    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

    try:
        from openai import OpenAI  # type: ignore[import-not-found]
    except ImportError:
        print(
            "OpenAI step skipped: 'openai' package not installed (pip install openai).",
            file=sys.stderr,
        )
        return

    try:
        from app.config import settings
    except ImportError as exc:
        print(f"OpenAI step skipped: import error ({exc}).", file=sys.stderr)
        return

    api_key = getattr(settings, "openai_api_key", None) or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print(
            "OpenAI step skipped: OPENAI_API_KEY not set in environment / .env.",
            file=sys.stderr,
        )
        return
    if model_override:
        model = model_override
    else:
        model = (
            getattr(settings, "openai_model", None)
            or os.getenv("OPENAI_MODEL")
            or DEFAULT_OPENAI_MODEL
        )

    triples, classes, subclasses, novas = _load_huge_label_universe(huge_data_csv)
    if not (classes or subclasses or novas):
        print(
            f"OpenAI step skipped: no class/subclass/nova labels found in {huge_data_csv}.",
            file=sys.stderr,
        )
        return

    triple_lookup: dict[str, tuple[str, str, str]] = {}
    for cls, sub, nova in triples:
        if cls:
            triple_lookup.setdefault(cls, (cls, sub, nova))
        if sub:
            triple_lookup.setdefault(f"sub::{sub}", (cls, sub, nova))

    with classified_csv.open(newline="", encoding="utf-8-sig", errors="replace") as f_in:
        all_rows = list(csv.DictReader(f_in))
    if not all_rows:
        print(f"OpenAI step: {classified_csv} has no data rows.")
        return
    first = all_rows[0]
    missing = [c for c in CLASSIFIED_REQUIRED_READ_COLUMNS if c not in first.keys()]
    if missing:
        raise ValueError(
            f"{classified_csv} is missing columns {missing!r}; "
            f"found {list(first.keys())!r}",
        )
    fieldnames = list(NUTRIENTS_WITH_CLASS_FIELDS)

    total_rows = len(all_rows)
    todo: list[tuple[int, str]] = []  # (row_index, Food Name)
    for i, row in enumerate(all_rows):
        if (row.get("class_name") or "").strip():
            continue
        food = (row.get("Food Name") or "").strip()
        if food:
            todo.append((i, food))
    rows_to_predict = len(todo)
    print(
        f"OpenAI backfill: {total_rows} rows total, {rows_to_predict} need classification "
        f"(model={model}, workers={workers}). Running…",
        flush=True,
    )

    client = OpenAI(api_key=api_key)

    out_rows: list[dict[str, str]] = [
        {k: (r.get(k) or "").strip() for k in NUTRIENTS_WITH_CLASS_FIELDS}
        for r in all_rows
    ]
    already_set = sum(1 for r in out_rows if (r.get("class_name") or "").strip())
    no_name = sum(
        1
        for r in out_rows
        if not (r.get("class_name") or "").strip() and not (r.get("Food Name") or "").strip()
    )
    filled = 0
    completed = 0

    def _classify(food_name: str) -> tuple[str, str, str, int, str] | None:
        return _openai_classify_one(
            client=client,
            model=model,
            food_name=food_name,
            classes=classes,
            subclasses=subclasses,
            novas=novas,
        )

    last_print = time.monotonic()
    print_every_rows = max(10, rows_to_predict // 50 or 10)
    print_every_seconds = 5.0

    if rows_to_predict > 0:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        max_workers = max(1, int(workers))
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            future_to_idx = {
                ex.submit(_classify, food): idx for idx, food in todo
            }
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                completed += 1
                try:
                    result = fut.result()
                except Exception as exc:  # pragma: no cover - safety net
                    print(f"  worker error on row {idx}: {exc}", file=sys.stderr)
                    result = None

                now = time.monotonic()
                if (
                    completed == 1
                    or completed % print_every_rows == 0
                    or completed == rows_to_predict
                    or (now - last_print) >= print_every_seconds
                ):
                    print(
                        f"  classified {completed}/{rows_to_predict} (filled so far: {filled})",
                        flush=True,
                    )
                    last_print = now

                if result is None:
                    continue

                cls_s, sub_s, nova_s, conf_int, _reason = result
                if cls_s and (not sub_s or not nova_s):
                    anchor = triple_lookup.get(cls_s)
                    if anchor is not None:
                        if not sub_s:
                            sub_s = anchor[1]
                        if not nova_s:
                            nova_s = anchor[2]
                elif sub_s and (not cls_s or not nova_s):
                    anchor = triple_lookup.get(f"sub::{sub_s}")
                    if anchor is not None:
                        if not cls_s:
                            cls_s = anchor[0]
                        if not nova_s:
                            nova_s = anchor[2]

                row_out = out_rows[idx]
                row_out["class_name"] = cls_s
                row_out["subclass_name"] = sub_s
                row_out["nova"] = nova_s
                if cls_s or sub_s or nova_s:
                    filled += 1
                    row_out[CLASSIFICATION_SOURCE_COLUMN] = "openai"
                    row_out[CLASSIFICATION_CONFIDENCE_COLUMN] = _confidence_to_str(conf_int)
                    row_out[NEEDS_REVIEW_COLUMN] = _needs_review_str(
                        conf_int < review_threshold,
                    )

    still_empty = sum(
        1
        for r in out_rows
        if not (r.get("class_name") or "").strip()
    )
    flagged_review = sum(
        1
        for r in out_rows
        if r.get(NEEDS_REVIEW_COLUMN) == "true" and r.get(CLASSIFICATION_SOURCE_COLUMN) == "openai"
    )

    tmp_path = classified_csv.with_suffix(classified_csv.suffix + ".tmp")
    try:
        with tmp_path.open("w", newline="", encoding="utf-8", errors="replace") as f_out:
            writer = csv.DictWriter(f_out, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(out_rows)
        tmp_path.replace(classified_csv)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise OSError(f"Could not write {classified_csv}: {exc}") from exc

    print(
        f"OpenAI backfill on {classified_csv.name}: "
        f"already had class {already_set}, filled {filled}, still missing class {still_empty} "
        f"(no product name: {no_name}). "
        f"openai rows flagged needs_review (conf < {review_threshold}): {flagged_review}.",
    )


def step_bilstm_fill_missing_classification(
    classified_csv: Path,
    *,
    review_threshold: int = DEFAULT_REVIEW_CONFIDENCE_THRESHOLD,
) -> None:
    """Fill empty class_name / subclass_name / nova using foodclasses BiLSTM (skips rows already set).

    Maps the model's class_confidence (0-1) to a 1-5 integer ``classification_confidence`` and
    flags ``needs_review`` when below ``review_threshold``.
    """
    if not classified_csv.is_file():
        raise FileNotFoundError(f"Classified CSV not found: {classified_csv}")

    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))

    os.environ.setdefault("FOODCLASSES_BILSTM_ENABLED", "true")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")

    try:
        import tensorflow as tf  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        print(
            "BiLSTM step skipped: TensorFlow is not installed (required to load foodclasses_model.pkl).",
            file=sys.stderr,
        )
        return

    try:
        from app.config import settings
        from app.services.foodclasses_bilstm_inference import (
            predict_foodclasses_from_product_text,
        )
    except ImportError as exc:
        print(f"BiLSTM step skipped: import error ({exc}).", file=sys.stderr)
        return

    if not settings.foodclasses_bilstm_model_pkl.is_file():
        print(
            f"BiLSTM step skipped: model not found at {settings.foodclasses_bilstm_model_pkl}.",
            file=sys.stderr,
        )
        return

    with classified_csv.open(newline="", encoding="utf-8-sig", errors="replace") as f_in:
        all_rows = list(csv.DictReader(f_in))
    if not all_rows:
        print(f"BiLSTM step: {classified_csv} has no data rows.")
        return
    first = all_rows[0]
    missing = [c for c in CLASSIFIED_REQUIRED_READ_COLUMNS if c not in first.keys()]
    if missing:
        raise ValueError(
            f"{classified_csv} is missing columns {missing!r}; "
            f"found {list(first.keys())!r}",
        )
    fieldnames = list(NUTRIENTS_WITH_CLASS_FIELDS)

    total_rows = len(all_rows)
    rows_to_predict = sum(
        1
        for r in all_rows
        if not (r.get("class_name") or "").strip() and (r.get("Food Name") or "").strip()
    )
    print(
        f"BiLSTM backfill: {total_rows} rows total, {rows_to_predict} need prediction. Running…",
        flush=True,
    )

    out_rows: list[dict[str, str]] = []
    already_set = 0
    filled = 0
    still_empty = 0
    no_name = 0
    predicted = 0
    progress_every = max(50, rows_to_predict // 20 or 50)

    for row in all_rows:
        base = {k: (row.get(k) or "").strip() for k in NUTRIENTS_WITH_CLASS_FIELDS}
        if (base.get("class_name") or "").strip():
            already_set += 1
            out_rows.append(base)
            continue

        food = (base.get("Food Name") or "").strip()
        if not food:
            no_name += 1
            still_empty += 1
            out_rows.append(base)
            continue

        pred = predict_foodclasses_from_product_text(food, None)
        predicted += 1
        if predicted == 1 or predicted % progress_every == 0 or predicted == rows_to_predict:
            print(
                f"  predicted {predicted}/{rows_to_predict} (filled so far: {filled})",
                flush=True,
            )
        if pred is None:
            still_empty += 1
            out_rows.append(base)
            continue

        base["class_name"] = (pred.class_name or "").strip()
        base["subclass_name"] = (pred.subclass_name or "").strip()
        base["nova"] = (pred.nova_label or "").strip()
        if base["class_name"]:
            filled += 1
            base[CLASSIFICATION_SOURCE_COLUMN] = "bilstm"
            try:
                prob = float(getattr(pred, "class_confidence", 0.0) or 0.0)
            except (TypeError, ValueError):
                prob = 0.0
            if prob >= 0.90:
                conf_int = 5
            elif prob >= 0.75:
                conf_int = 4
            elif prob >= 0.55:
                conf_int = 3
            elif prob >= 0.35:
                conf_int = 2
            else:
                conf_int = 1
            base[CLASSIFICATION_CONFIDENCE_COLUMN] = _confidence_to_str(conf_int)
            base[NEEDS_REVIEW_COLUMN] = _needs_review_str(conf_int < review_threshold)
        else:
            still_empty += 1
        out_rows.append(base)

    tmp_path = classified_csv.with_suffix(classified_csv.suffix + ".tmp")
    try:
        with tmp_path.open("w", newline="", encoding="utf-8", errors="replace") as f_out:
            writer = csv.DictWriter(f_out, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(out_rows)
        tmp_path.replace(classified_csv)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise OSError(f"Could not write {classified_csv}: {exc}") from exc

    print(
        f"BiLSTM backfill on {classified_csv.name}: "
        f"already had class {already_set}, filled {filled}, still missing class {still_empty} "
        f"(no product name: {no_name}).",
    )


REVIEW_OUTPUT_COLUMNS = (
    "Food Name",
    *CLASSIFICATION_COLUMNS,
    CLASSIFICATION_SOURCE_COLUMN,
    CLASSIFICATION_CONFIDENCE_COLUMN,
    NEEDS_REVIEW_COLUMN,
    # manual review fields, left empty for the reviewer to fill in
    "corrected_class_name",
    "corrected_subclass_name",
    "corrected_nova",
    "reviewer_note",
)

MANUAL_CORRECTIONS_KEY_COLUMN = "Food Name"
MANUAL_CORRECTIONS_REQUIRED_COLUMNS = (
    MANUAL_CORRECTIONS_KEY_COLUMN,
    *CLASSIFICATION_COLUMNS,
)
MANUAL_CORRECTIONS_OPTIONAL_COLUMNS = (NEEDS_REVIEW_COLUMN, "reviewer_note")
MANUAL_CORRECTIONS_FIELDS = (
    *MANUAL_CORRECTIONS_REQUIRED_COLUMNS,
    *MANUAL_CORRECTIONS_OPTIONAL_COLUMNS,
)


def step_apply_manual_corrections(
    classified_csv: Path,
    corrections_csv: Path,
) -> None:
    """Overlay hand-curated overrides onto the classified CSV.

    The corrections file has columns ``Food Name, class_name, subclass_name, nova,
    needs_review (optional), reviewer_note (optional)``. Rows whose ``Food Name`` matches
    (case-insensitive normalized) are overwritten. ``classification_source`` becomes
    ``manual`` and ``classification_confidence`` becomes 5 (or 2 when ``needs_review`` is
    "true" in the corrections file). Empty class/subclass/nova in a correction row will
    BLANK out the corresponding field on the classified row. Missing correction file is a
    no-op.
    """
    if not classified_csv.is_file():
        raise FileNotFoundError(f"Classified CSV not found: {classified_csv}")
    if not corrections_csv.is_file():
        print(
            f"Manual corrections step: {corrections_csv} not found — skipping (no overrides to apply).",
            file=sys.stderr,
        )
        return

    with corrections_csv.open(newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            print(
                f"Manual corrections step: {corrections_csv} has no header — skipping.",
                file=sys.stderr,
            )
            return
        missing = [
            c for c in MANUAL_CORRECTIONS_REQUIRED_COLUMNS if c not in reader.fieldnames
        ]
        if missing:
            raise ValueError(
                f"{corrections_csv} is missing columns {missing!r}; "
                f"found {list(reader.fieldnames)!r}",
            )
        overrides: dict[str, dict[str, str]] = {}
        dup_keys: list[str] = []
        for raw in reader:
            food = (raw.get(MANUAL_CORRECTIONS_KEY_COLUMN) or "").strip()
            if not food:
                continue
            key = normalize_product_match_key(food)
            if not key:
                continue
            entry = {
                "class_name": (raw.get("class_name") or "").strip(),
                "subclass_name": (raw.get("subclass_name") or "").strip(),
                "nova": (raw.get("nova") or "").strip(),
                NEEDS_REVIEW_COLUMN: (raw.get(NEEDS_REVIEW_COLUMN) or "").strip().lower(),
                "reviewer_note": (raw.get("reviewer_note") or "").strip(),
                "_food_display": food,
            }
            if key in overrides:
                dup_keys.append(food)
            overrides[key] = entry

    if not overrides:
        print(f"Manual corrections step: no usable rows in {corrections_csv}.", file=sys.stderr)
        return

    with classified_csv.open(newline="", encoding="utf-8-sig", errors="replace") as f_in:
        rows = list(csv.DictReader(f_in))
    if not rows:
        print(f"Manual corrections step: {classified_csv} has no data rows.")
        return

    out_rows: list[dict[str, str]] = []
    applied = 0
    blanked_subclasses = 0
    keys_seen: set[str] = set()
    for row in rows:
        base = {k: (row.get(k) or "").strip() for k in NUTRIENTS_WITH_CLASS_FIELDS}
        food = (base.get("Food Name") or "").strip()
        key = normalize_product_match_key(food)
        if key and key in overrides:
            keys_seen.add(key)
            ov = overrides[key]
            base["class_name"] = ov["class_name"]
            base["subclass_name"] = ov["subclass_name"]
            base["nova"] = ov["nova"]
            base[CLASSIFICATION_SOURCE_COLUMN] = "manual"
            needs_flag = ov[NEEDS_REVIEW_COLUMN] == "true"
            base[CLASSIFICATION_CONFIDENCE_COLUMN] = "2" if needs_flag else "5"
            base[NEEDS_REVIEW_COLUMN] = _needs_review_str(needs_flag)
            applied += 1
            if not ov["subclass_name"]:
                blanked_subclasses += 1
        out_rows.append(base)

    unmatched = [
        ov["_food_display"] for k, ov in overrides.items() if k not in keys_seen
    ]

    fieldnames = list(NUTRIENTS_WITH_CLASS_FIELDS)
    tmp_path = classified_csv.with_suffix(classified_csv.suffix + ".tmp")
    try:
        with tmp_path.open("w", newline="", encoding="utf-8", errors="replace") as f_out:
            writer = csv.DictWriter(f_out, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(out_rows)
        tmp_path.replace(classified_csv)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise OSError(f"Could not write {classified_csv}: {exc}") from exc

    print(
        f"Manual corrections: applied {applied}/{len(overrides)} overrides "
        f"from {corrections_csv.name} to {classified_csv.name} "
        f"(blanked subclass on {blanked_subclasses}). "
        f"Unmatched in classified: {len(unmatched)}.",
    )
    if dup_keys:
        print(
            f"  WARNING: {len(dup_keys)} duplicate keys in corrections file "
            f"(last row wins): {dup_keys[:5]!r}{'...' if len(dup_keys) > 5 else ''}",
            file=sys.stderr,
        )
    if unmatched:
        sample = unmatched[:8]
        print(
            f"  Note: {len(unmatched)} correction row(s) did not match any Food Name in classified: "
            f"{sample!r}{'...' if len(unmatched) > 8 else ''}",
            file=sys.stderr,
        )


def step_export_review(
    classified_csv: Path,
    review_csv: Path,
    *,
    sources: tuple[str, ...] = ("openai", "bilstm", "huge_data_fuzzy"),
    confidence_threshold: int = DEFAULT_REVIEW_CONFIDENCE_THRESHOLD,
    only_needs_review: bool = True,
) -> None:
    """Dump suspect classified rows to a separate CSV for manual review.

    A row is included when its ``classification_source`` is in ``sources`` AND
    (``only_needs_review`` and ``needs_review`` == "true") OR
    its ``classification_confidence`` is missing / below ``confidence_threshold``.
    """
    if not classified_csv.is_file():
        raise FileNotFoundError(f"Classified CSV not found: {classified_csv}")

    src_set = {s.strip() for s in sources if s.strip()}

    with classified_csv.open(newline="", encoding="utf-8-sig", errors="replace") as f_in:
        reader = csv.DictReader(f_in)
        if reader.fieldnames is None:
            raise ValueError(f"No header row in {classified_csv}")
        review_rows: list[dict[str, str]] = []
        considered = 0
        for row in reader:
            considered += 1
            src = (row.get(CLASSIFICATION_SOURCE_COLUMN) or "").strip()
            if src_set and src not in src_set:
                continue
            conf_raw = (row.get(CLASSIFICATION_CONFIDENCE_COLUMN) or "").strip()
            try:
                conf_int = int(conf_raw) if conf_raw else None
            except ValueError:
                conf_int = None
            needs_flag = (row.get(NEEDS_REVIEW_COLUMN) or "").strip().lower() == "true"
            include = False
            if only_needs_review and needs_flag:
                include = True
            elif conf_int is None or conf_int < confidence_threshold:
                include = True
            if not include:
                continue
            out: dict[str, str] = {k: "" for k in REVIEW_OUTPUT_COLUMNS}
            out["Food Name"] = (row.get("Food Name") or "").strip()
            for c in CLASSIFICATION_COLUMNS:
                out[c] = (row.get(c) or "").strip()
            out[CLASSIFICATION_SOURCE_COLUMN] = src
            out[CLASSIFICATION_CONFIDENCE_COLUMN] = conf_raw
            out[NEEDS_REVIEW_COLUMN] = "true" if needs_flag else (row.get(NEEDS_REVIEW_COLUMN) or "")
            review_rows.append(out)

    review_csv.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = review_csv.with_suffix(review_csv.suffix + ".tmp")
    try:
        with tmp_path.open("w", newline="", encoding="utf-8", errors="replace") as f_out:
            writer = csv.DictWriter(f_out, fieldnames=list(REVIEW_OUTPUT_COLUMNS))
            writer.writeheader()
            writer.writerows(review_rows)
        tmp_path.replace(review_csv)
    except OSError as exc:
        tmp_path.unlink(missing_ok=True)
        raise OSError(f"Could not write {review_csv}: {exc}") from exc

    print(
        f"Review export: scanned {considered} rows from {classified_csv.name}, "
        f"wrote {len(review_rows)} suspect rows to {review_csv} "
        f"(sources={sorted(src_set) or 'any'}, only_needs_review={only_needs_review}, "
        f"confidence_threshold={confidence_threshold}).",
    )


def step_classify_food_reference_nutrition(
    cleaned_csv: Path,
    classified_csv: Path,
    huge_data_csv: Path,
    *,
    fuzzy_cutoff: int = 90,
    review_threshold: int = DEFAULT_REVIEW_CONFIDENCE_THRESHOLD,
    workers: int = 8,
    model_override: str | None = None,
    run_openai: bool = True,
) -> None:
    """Classify the cleaned reference nutrient table.

    Runs the same two-step flow as the SKU table:
      1. ``step_join_huge_data_exact_description`` — exact + (optional) fuzzy match against
         ``huge_data_cleaned.csv`` to inherit class/subclass/nova.
      2. ``step_openai_fill_missing_classification`` — OpenAI fills the rest, restricted to
         the same allow-list.

    Inputs:
      - ``cleaned_csv``: file produced by :func:`step_clean_food_reference_nutrition`.
      - ``huge_data_csv``: cleaned huge_data file (also used by the SKU pipeline).
      - ``classified_csv``: output path; same schema as
        ``all_categories_nutrients_classified.csv``.
    """
    if not cleaned_csv.is_file():
        raise FileNotFoundError(
            f"Reference cleaned CSV not found: {cleaned_csv}. "
            f"Run --step clean_reference_nutrition first.",
        )
    if not huge_data_csv.is_file():
        raise FileNotFoundError(
            f"huge_data cleaned CSV not found: {huge_data_csv}. "
            f"Run --step clean_huge_descriptions first.",
        )

    step_join_huge_data_exact_description(
        huge_data_csv,
        cleaned_csv,
        classified_csv,
        fuzzy_cutoff=fuzzy_cutoff,
        review_threshold=review_threshold,
    )
    if run_openai:
        step_openai_fill_missing_classification(
            classified_csv,
            huge_data_csv,
            workers=workers,
            model_override=model_override,
            review_threshold=review_threshold,
        )


def run_all(
    data_database: Path,
    staged: Path,
    *,
    fuzzy_cutoff: int = 90,
    run_openai: bool = True,
    openai_workers: int = 8,
    openai_model: str | None = None,
    review_threshold: int = DEFAULT_REVIEW_CONFIDENCE_THRESHOLD,
    corrections_csv: Path | None = None,
) -> None:
    step_all_categories_nutrient_columns(
        data_database / "all_categories_combined.csv",
        staged / "all_categories_nutrients.csv",
    )
    huge_cleaned = staged / "huge_data_cleaned.csv"
    step_clean_huge_data_descriptions(
        data_database / "huge_data.csv",
        huge_cleaned,
    )
    classified = staged / "all_categories_nutrients_classified.csv"
    step_join_huge_data_exact_description(
        huge_cleaned,
        staged / "all_categories_nutrients.csv",
        classified,
        fuzzy_cutoff=fuzzy_cutoff,
        review_threshold=review_threshold,
    )
    if run_openai:
        step_openai_fill_missing_classification(
            classified,
            huge_cleaned,
            workers=openai_workers,
            model_override=openai_model,
            review_threshold=review_threshold,
        )
    overrides_path = corrections_csv or (data_database / "manual_corrections.csv")
    step_apply_manual_corrections(classified, overrides_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Clean data_database CSVs for Postgres.")
    parser.add_argument(
        "--data-database",
        type=Path,
        default=_ROOT / "data_database",
        help="Folder containing raw CSVs (default: <repo>/data_database)",
    )
    parser.add_argument(
        "--staged",
        type=Path,
        default=_ROOT / "data_database" / "staged",
        help="Output folder for staged CSVs (default: <repo>/data_database/staged)",
    )
    parser.add_argument(
        "--step",
        choices=(
            "all",
            "prepare_clean",
            "all_categories_columns",
            "clean_huge_descriptions",
            "join_huge_classification",
            "openai_classify",
            "bilstm_classify",
            "export_review",
            "apply_manual_corrections",
            "clean_reference_nutrition",
            "classify_reference_nutrition",
            "apply_reference_corrections",
            "reference_all",
        ),
        default="all",
        help="Run one step only, or 'all' for the full pipeline (default: all).",
    )
    parser.add_argument(
        "--preview-rows",
        type=int,
        default=10,
        metavar="N",
        help="For prepare_clean: sample rows to print per file (default: 10).",
    )
    parser.add_argument(
        "--fuzzy-cutoff",
        type=int,
        default=90,
        metavar="SCORE",
        help=(
            "For join_huge_classification / all: RapidFuzz WRatio minimum (0-100) for fuzzy "
            "fallback after exact match; 0 disables fuzzy (default: 90)."
        ),
    )
    parser.add_argument(
        "--no-openai",
        action="store_true",
        help="For --step all: skip OpenAI backfill on all_categories_nutrients_classified.csv.",
    )
    parser.add_argument(
        "--openai-workers",
        type=int,
        default=8,
        metavar="N",
        help="Parallel OpenAI requests for openai_classify / all (default: 8).",
    )
    parser.add_argument(
        "--openai-model",
        type=str,
        default=None,
        metavar="MODEL",
        help=(
            "Override OpenAI model for openai_classify / all (e.g. 'gpt-4o' for higher accuracy). "
            "Falls back to OPENAI_MODEL env / settings, then 'gpt-4o-mini'."
        ),
    )
    parser.add_argument(
        "--review-confidence-threshold",
        type=int,
        default=DEFAULT_REVIEW_CONFIDENCE_THRESHOLD,
        metavar="N",
        help=(
            "Confidence (1-5) below which rows are flagged needs_review by join / openai / bilstm "
            "and selected by export_review (default: 4)."
        ),
    )
    parser.add_argument(
        "--review-out",
        type=Path,
        default=None,
        metavar="PATH",
        help="For export_review: output CSV path (default: <staged>/openai_review.csv).",
    )
    parser.add_argument(
        "--review-sources",
        type=str,
        default="openai,bilstm,huge_data_fuzzy",
        metavar="LIST",
        help=(
            "For export_review: comma-separated classification_source values to include "
            "(default: openai,bilstm,huge_data_fuzzy). Pass 'any' to include all sources."
        ),
    )
    parser.add_argument(
        "--review-include-passing",
        action="store_true",
        help=(
            "For export_review: also include rows whose confidence is below threshold even if "
            "needs_review is false. Default behavior is needs_review=true rows only."
        ),
    )
    parser.add_argument(
        "--corrections",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Path to manual corrections CSV (Food Name + class/subclass/nova overrides). "
            "Default: <data-database>/manual_corrections.csv. Used by apply_manual_corrections / all."
        ),
    )
    args = parser.parse_args()
    data_database: Path = args.data_database.resolve()
    staged: Path = args.staged.resolve()

    if args.step == "all_categories_columns":
        step_all_categories_nutrient_columns(
            data_database / "all_categories_combined.csv",
            staged / "all_categories_nutrients.csv",
        )
        return
    if args.step == "clean_huge_descriptions":
        step_clean_huge_data_descriptions(
            data_database / "huge_data.csv",
            staged / "huge_data_cleaned.csv",
        )
        return
    if args.step == "prepare_clean":
        nut = staged / "all_categories_nutrients.csv"
        huge = staged / "huge_data_cleaned.csv"
        step_all_categories_nutrient_columns(
            data_database / "all_categories_combined.csv",
            nut,
        )
        step_clean_huge_data_descriptions(
            data_database / "huge_data.csv",
            huge,
        )
        print_clean_outputs_inspection(
            nut,
            huge,
            preview_rows=max(1, args.preview_rows),
        )
        return
    review_threshold = max(1, min(5, int(args.review_confidence_threshold)))

    if args.step == "join_huge_classification":
        huge = staged / "huge_data_cleaned.csv"
        if not huge.is_file():
            huge = data_database / "huge_data.csv"
            print(
                f"Note: {staged / 'huge_data_cleaned.csv'} missing — "
                f"joining from raw {huge.name} (run clean_huge_descriptions for cleaned descriptions).",
                file=sys.stderr,
            )
        classified = staged / "all_categories_nutrients_classified.csv"
        step_join_huge_data_exact_description(
            huge,
            staged / "all_categories_nutrients.csv",
            classified,
            fuzzy_cutoff=max(0, min(100, args.fuzzy_cutoff)),
            review_threshold=review_threshold,
        )
        return
    if args.step == "openai_classify":
        step_openai_fill_missing_classification(
            staged / "all_categories_nutrients_classified.csv",
            staged / "huge_data_cleaned.csv",
            workers=max(1, args.openai_workers),
            model_override=args.openai_model,
            review_threshold=review_threshold,
        )
        return
    if args.step == "bilstm_classify":
        step_bilstm_fill_missing_classification(
            staged / "all_categories_nutrients_classified.csv",
            review_threshold=review_threshold,
        )
        return
    if args.step == "apply_manual_corrections":
        corrections_path = (
            args.corrections.resolve()
            if args.corrections is not None
            else (data_database / "manual_corrections.csv")
        )
        step_apply_manual_corrections(
            staged / "all_categories_nutrients_classified.csv",
            corrections_path,
        )
        return
    if args.step == "export_review":
        out_path: Path = (
            args.review_out.resolve()
            if args.review_out is not None
            else (staged / "openai_review.csv")
        )
        sources_arg = (args.review_sources or "").strip()
        if not sources_arg or sources_arg.lower() == "any":
            sources_tuple: tuple[str, ...] = ()
        else:
            sources_tuple = tuple(s.strip() for s in sources_arg.split(",") if s.strip())
        step_export_review(
            staged / "all_categories_nutrients_classified.csv",
            out_path,
            sources=sources_tuple,
            confidence_threshold=review_threshold,
            only_needs_review=not args.review_include_passing,
        )
        return
    if args.step == "clean_reference_nutrition":
        step_clean_food_reference_nutrition(
            data_database / FOOD_REFERENCE_SOURCE_FILE,
            staged / FOOD_REFERENCE_CLEANED_FILE,
        )
        return
    if args.step == "classify_reference_nutrition":
        step_classify_food_reference_nutrition(
            staged / FOOD_REFERENCE_CLEANED_FILE,
            staged / FOOD_REFERENCE_CLASSIFIED_FILE,
            staged / "huge_data_cleaned.csv",
            fuzzy_cutoff=max(0, min(100, args.fuzzy_cutoff)),
            review_threshold=review_threshold,
            workers=max(1, args.openai_workers),
            model_override=args.openai_model,
            run_openai=not args.no_openai,
        )
        ref_corr = data_database / FOOD_REFERENCE_CORRECTIONS_FILE
        if ref_corr.is_file():
            step_apply_manual_corrections(
                staged / FOOD_REFERENCE_CLASSIFIED_FILE,
                ref_corr,
            )
        return
    if args.step == "apply_reference_corrections":
        ref_corr = (
            args.corrections.resolve()
            if args.corrections is not None
            else (data_database / FOOD_REFERENCE_CORRECTIONS_FILE)
        )
        step_apply_manual_corrections(
            staged / FOOD_REFERENCE_CLASSIFIED_FILE,
            ref_corr,
        )
        return
    if args.step == "reference_all":
        step_clean_food_reference_nutrition(
            data_database / FOOD_REFERENCE_SOURCE_FILE,
            staged / FOOD_REFERENCE_CLEANED_FILE,
        )
        # Need the huge_data cleaned file for classification — produce it on demand if missing.
        huge_cleaned = staged / "huge_data_cleaned.csv"
        if not huge_cleaned.is_file():
            step_clean_huge_data_descriptions(
                data_database / "huge_data.csv",
                huge_cleaned,
            )
        step_classify_food_reference_nutrition(
            staged / FOOD_REFERENCE_CLEANED_FILE,
            staged / FOOD_REFERENCE_CLASSIFIED_FILE,
            huge_cleaned,
            fuzzy_cutoff=max(0, min(100, args.fuzzy_cutoff)),
            review_threshold=review_threshold,
            workers=max(1, args.openai_workers),
            model_override=args.openai_model,
            run_openai=not args.no_openai,
        )
        ref_corr = data_database / FOOD_REFERENCE_CORRECTIONS_FILE
        if ref_corr.is_file():
            step_apply_manual_corrections(
                staged / FOOD_REFERENCE_CLASSIFIED_FILE,
                ref_corr,
            )
        return

    run_all(
        data_database,
        staged,
        fuzzy_cutoff=max(0, min(100, args.fuzzy_cutoff)),
        run_openai=not args.no_openai,
        openai_workers=max(1, args.openai_workers),
        openai_model=args.openai_model,
        review_threshold=review_threshold,
        corrections_csv=args.corrections.resolve() if args.corrections else None,
    )


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, ValueError, OSError) as e:
        print(e, file=sys.stderr)
        raise SystemExit(1) from e
