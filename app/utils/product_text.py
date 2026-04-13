"""Helpers for building product text used by matching / model inference."""

from __future__ import annotations

from app.utils.pack_description import normalize_pack_description


def _clean(v: str | None) -> str:
    return str(v or "").strip()


def compose_product_query_text(name: str | None, brand: str | None) -> str | None:
    """
    Build one query string for downstream matching:
    - both missing -> None
    - one present -> that one
    - both present and same after normalize+casefold -> one value
    - both present and different -> "BRAND NAME"
    """
    n = _clean(name)
    b = _clean(brand)
    if not n and not b:
        return None
    if not n:
        return b
    if not b:
        return n

    n_norm = normalize_pack_description(n).casefold()
    b_norm = normalize_pack_description(b).casefold()
    if n_norm and b_norm and n_norm == b_norm:
        return n
    return f"{b} {n}".strip()
