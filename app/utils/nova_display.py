"""
Canonical NOVA labels for API output using ``models/nova_labels.json``.

The JSON maps softmax head indices (keys ``"0"``..``"N-1"``) to official display strings.
When the BiLSTM NOVA head has the same number of outputs as keys, the argmax index selects
the string directly. Values from the reference catalog are normalized to the same strings
when they match digits 1–4, encoder-style text, or the text after the em dash in a row.
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=4)
def _nova_label_map(path_str: str) -> dict[str, str] | None:
    p = Path(path_str)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("Could not load NOVA labels JSON %s: %s", p, e)
        return None
    if not isinstance(data, dict) or not data:
        return None
    out: dict[str, str] = {}
    for k, v in data.items():
        if v is None:
            continue
        out[str(k)] = str(v).strip()
    return out or None


def _sorted_numeric_keys(m: dict[str, str]) -> list[str]:
    def _key(x: str) -> int:
        return int(x) if x.isdigit() else 10**9

    return sorted(m.keys(), key=_key)


def _match_raw_to_map(raw: str, m: dict[str, str]) -> str | None:
    """Map a DB or encoder string to a canonical JSON value when unambiguous."""
    r = raw.strip()
    if not r:
        return None
    rl = r.casefold()

    # Single digit 1–4 means NOVA *group* 1..4 → JSON keys "0".."3" (not JSON key "1".."3").
    if len(r) == 1 and r in "1234":
        key = str(int(r) - 1)
        if key in m:
            return m[key]

    if r == "0" and r in m:
        return m[r]

    if r in m and r not in {"1", "2", "3", "4"}:
        return m[r]

    for _k in _sorted_numeric_keys(m):
        full = m[_k]
        fl = full.casefold()
        if fl == rl:
            return full
        if "—" in full:
            part = full.split("—", 1)[1].strip()
            pl = part.casefold()
            if pl == rl or pl in rl or rl in pl:
                return full
        elif " - " in full:
            part = full.split(" - ", 1)[1].strip()
            pl = part.casefold()
            if pl == rl or pl in rl or rl in pl:
                return full

    m_num = re.match(r"^\s*nova\s*(\d)\s*$", r, re.IGNORECASE)
    if m_num:
        idx = int(m_num.group(1)) - 1
        if 0 <= idx <= 3:
            key = str(idx)
            if key in m:
                return m[key]

    return None


def normalize_nova_for_api(
    raw: str | None,
    *,
    softmax_index: int | None = None,
    softmax_size: int | None = None,
) -> str | None:
    """
    Return the canonical NOVA display string from ``nova_labels.json`` when possible.

    - **Model path:** when ``softmax_index`` and ``softmax_size`` are set and
      ``softmax_size`` equals the number of entries in the JSON, use
      ``JSON[str(softmax_index)]``.
    - **Otherwise:** match ``raw`` (catalog or encoder class name) to a JSON value
      (digits 1–4, text after ``—``, substring match, ``NOVA n``).
    - If the JSON file is missing or no rule matches, return ``raw`` stripped or ``None``.
    """
    m = _nova_label_map(str(settings.nova_labels_json))
    if not m:
        return raw.strip() if raw and str(raw).strip() else None

    n_keys = len(m)
    if (
        softmax_index is not None
        and softmax_size is not None
        and softmax_size == n_keys
        and 0 <= softmax_index < n_keys
    ):
        key = str(softmax_index)
        if key in m:
            return m[key]

    if raw is None or not str(raw).strip():
        return None

    matched = _match_raw_to_map(str(raw), m)
    if matched is not None:
        return matched

    return str(raw).strip()
