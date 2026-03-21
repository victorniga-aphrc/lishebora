"""
Resolve supermarket POS class / subclass / NOVA from OCR product info.

1) SKU line: match OCR name / brand+name to CSV ``description`` (exact, then fuzzy).
   OCR text and CSV rows are normalized with the same pack-size stripping as the
   build script. Fuzzy uses max(WRatio, partial_ratio) for spelling / variant gaps.

2) Taxonomy fallback: if no SKU hit, match OCR ``category`` to distinct POS
   ``subclass_name`` then ``class_name`` (token_set_ratio). This lands "Fruit Drink"
   in the fruit-drink subclass even when the product SKU is not in the file.
"""

from __future__ import annotations

import csv
import logging
from typing import Any

from rapidfuzz import fuzz, process

from app.config import settings
from app.utils.pos_description import normalize_pack_description

logger = logging.getLogger(__name__)


def _sku_description_scorer(query: str, choice: str, **kwargs: Any) -> float:
    """Prefer matches where OCR text is a prefix/substring of the POS line."""
    return max(
        float(fuzz.WRatio(query, choice, **kwargs)),
        float(fuzz.partial_ratio(query, choice, **kwargs)),
    )


class _LookupData:
    __slots__ = (
        "loaded",
        "rows",
        "norm_to_row",
        "descriptions",
        "taxonomy_subclass_entries",
        "taxonomy_class_entries",
    )

    def __init__(self) -> None:
        self.loaded = False
        self.rows: list[dict[str, Any]] = []
        self.norm_to_row: dict[str, dict[str, Any]] = {}
        self.descriptions: list[str] = []
        self.taxonomy_subclass_entries: list[tuple[str, dict[str, Any]]] = []
        self.taxonomy_class_entries: list[tuple[str, dict[str, Any]]] = []


_data = _LookupData()


def _load_csv() -> None:
    if _data.loaded:
        return
    _data.loaded = True
    path = settings.supermarket_lookup_csv
    if not path.exists():
        logger.warning("Supermarket lookup CSV not found at %s — classification disabled.", path)
        return
    try:
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                desc = (row.get("description") or "").strip()
                if not desc:
                    continue
                rec = {
                    "description": desc,
                    "class_name": (row.get("class_name") or "").strip() or None,
                    "subclass_name": (row.get("subclass_name") or "").strip() or None,
                    "nova": (row.get("nova") or "").strip() or None,
                }
                n = normalize_pack_description(desc)
                if not n:
                    continue
                if n not in _data.norm_to_row:
                    _data.norm_to_row[n] = rec
                    _data.rows.append(rec)
                    _data.descriptions.append(desc)
    except OSError as e:
        logger.exception("Failed to load supermarket lookup CSV: %s", e)
        return

    # Unique (class, subclass) → first SKU row (for taxonomy fallback + representative description)
    seen_pairs: set[tuple[str, str]] = set()
    for r in _data.rows:
        c = r.get("class_name") or ""
        s = r.get("subclass_name") or ""
        if not s:
            continue
        key = (c, s)
        if key in seen_pairs:
            continue
        seen_pairs.add(key)
        label = s.replace("/", " ").strip()
        _data.taxonomy_subclass_entries.append((label, r))

    seen_class: set[str] = set()
    for r in _data.rows:
        c = r.get("class_name") or ""
        if not c or c in seen_class:
            continue
        seen_class.add(c)
        label = c.replace("/", " ").strip()
        _data.taxonomy_class_entries.append((label, r))


def _match_by_sku_descriptions(
    product_info: Any,
    min_score: float,
) -> Any | None:
    """Match name / brand+name to POS ``description`` column."""
    from app.models import SupermarketClassification

    queries: list[tuple[str, str]] = []
    if product_info and product_info.name and product_info.name.strip():
        queries.append(("name", product_info.name.strip()))
    if (
        product_info
        and product_info.brand
        and product_info.name
        and product_info.brand.strip()
        and product_info.name.strip()
    ):
        combined = f"{product_info.brand.strip()} {product_info.name.strip()}".strip()
        if combined != product_info.name.strip():
            queries.append(("combined", combined))

    if not queries or not _data.descriptions:
        return None

    for source, q in queries:
        nq = normalize_pack_description(q)
        if nq in _data.norm_to_row:
            row = _data.norm_to_row[nq]
            return SupermarketClassification(
                class_name=row["class_name"],
                subclass_name=row["subclass_name"],
                nova=row["nova"],
                matched_description=row["description"],
                match_method=f"exact_{source}",
                match_score=None,
            )

    best_desc: str | None = None
    best_score = -1.0
    best_source = ""
    for source, q in queries:
        q_norm = normalize_pack_description(q)
        hit = process.extractOne(
            q_norm,
            _data.descriptions,
            scorer=_sku_description_scorer,
            score_cutoff=min_score,
        )
        if hit is not None:
            match_s, score, _ = hit
            if score > best_score:
                best_score = float(score)
                best_desc = match_s
                best_source = source

    if best_desc is None:
        return None

    nbest = normalize_pack_description(best_desc)
    row = _data.norm_to_row.get(nbest)
    if row is None:
        row = next((r for r in _data.rows if r["description"] == best_desc), None)
    if row is None:
        return None

    return SupermarketClassification(
        class_name=row["class_name"],
        subclass_name=row["subclass_name"],
        nova=row["nova"],
        matched_description=row["description"],
        match_method=f"fuzzy_{best_source}",
        match_score=best_score,
    )


def _match_by_category_taxonomy(
    product_info: Any,
    min_score: float,
) -> Any | None:
    """Map OCR ``category`` to a POS subclass or class (distinct labels in CSV)."""
    from app.models import SupermarketClassification

    if not product_info:
        return None
    cat = (product_info.category or "").strip()
    if len(cat) < 3:
        return None

    if _data.taxonomy_subclass_entries:
        labels = [e[0] for e in _data.taxonomy_subclass_entries]
        hit = process.extractOne(
            cat,
            labels,
            scorer=fuzz.token_set_ratio,
            score_cutoff=min_score,
        )
        if hit is not None:
            _match_s, score, idx = hit
            row = _data.taxonomy_subclass_entries[idx][1]
            return SupermarketClassification(
                class_name=row["class_name"],
                subclass_name=row["subclass_name"],
                nova=row["nova"],
                matched_description=row["description"],
                match_method="taxonomy_subclass_from_category",
                match_score=float(score),
            )

    if _data.taxonomy_class_entries:
        labels = [e[0] for e in _data.taxonomy_class_entries]
        hit = process.extractOne(
            cat,
            labels,
            scorer=fuzz.token_set_ratio,
            score_cutoff=min_score,
        )
        if hit is not None:
            _match_s, score, idx = hit
            row = _data.taxonomy_class_entries[idx][1]
            return SupermarketClassification(
                class_name=row["class_name"],
                subclass_name=row["subclass_name"],
                nova=row["nova"],
                matched_description=row["description"],
                match_method="taxonomy_class_from_category",
                match_score=float(score),
            )

    return None


def lookup_supermarket_classification(
    product_info: Any | None,
) -> Any | None:
    """Return taxonomy match for product, or None if no CSV / no queries / no match."""
    _load_csv()
    if not _data.rows:
        return None

    sku = _match_by_sku_descriptions(
        product_info,
        float(settings.supermarket_fuzzy_min_score),
    )
    if sku is not None:
        return sku

    return _match_by_category_taxonomy(
        product_info,
        float(settings.supermarket_taxonomy_fuzzy_min_score),
    )
