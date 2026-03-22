"""
Normalize POS / label product lines for matching.

Strips pack sizes and common packaging tokens so the same logical product (per 100g/ml)
maps to one key; used by the lookup CSV build and runtime supermarket matching.
"""

from __future__ import annotations

import re

# Multipack e.g. 6X300ML, 12 X 500 ML
_MULTI = re.compile(
    r"\b\d+\s*X\s*\d+(?:\.\d+)?\s*(?:ML|LTR|CL|KG|KGS|GM|GR|GMS|G|L)\b",
    re.IGNORECASE,
)
# Volume×count (reverse order): 1.5LX6, 1LX6, 500MLX6, 12 X 500 ML (overlap handled by _MULTI first)
_VOL_X_COUNT = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:ML|LTR)\s*X\s*\d+\b|\b\d+(?:\.\d+)?\s*L\s*X\s*\d+\b",
    re.IGNORECASE,
)
# Stray codes like G14G (not a normal \d+G size)
_G_NUM_G = re.compile(r"\bG\d+(?:\.\d+)?G\b", re.IGNORECASE)
# Piece/pack with apostrophe: 10'S, 6'S (also glued: …EUCALYPTUS10'S)
_APOST_S = re.compile(
    r"\b\d+\s*'S\b|(?<=[A-Z])\d+\s*'S\b",
    re.IGNORECASE,
)
# Size jammed to preceding letters (no space): POUCH200ML, OIL500G, DRESS237G
_GLUED_SIZE_UNITS = re.compile(
    r"(?<=[A-Z])\d+(?:\.\d+)?\s*(?:ML|LTR|CL|KG|KGS|GM|GR|GMS)\b",
    re.IGNORECASE,
)
_GLUED_SIZE_G = re.compile(
    r"(?<=[A-Z])\d+(?:\.\d+)?\s*G\b(?![A-Z0-9/])",
    re.IGNORECASE,
)
_GLUED_SIZE_L = re.compile(
    r"(?<=[A-Z])\d+(?:\.\d+)?\s*L\b(?![A-Z0-9/])",
    re.IGNORECASE,
)
_SIZE_UNITS = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:ML|LTR|CL|KG|KGS|GM|GR|GMS)\b",
    re.IGNORECASE,
)
_SIZE_L = re.compile(r"\b\d+(?:\.\d+)?\s*L\b(?![A-Z0-9/])", re.IGNORECASE)
# Plain "35G" at end of token (not "35G/" — handled separately)
_SIZE_G = re.compile(r"\b\d+(?:\.\d+)?\s*G\b(?![A-Z0-9/])", re.IGNORECASE)
# Export quirk: weight then slash, e.g. 200G/, 35G/, 72.25G/
_G_WITH_SLASH = re.compile(r"\b\d+(?:\.\d+)?\s*G\s*/", re.IGNORECASE)
# Piece counts: 300PCS, 6PCS, (12PCS), 65X5PCS
_PCS = re.compile(
    r"\(\s*\d+\s*PCS\s*\)|"
    r"\b\d+\s*X\s*\d+\s*PCS\b|"
    r"\b\d+\s*PCS\b",
    re.IGNORECASE,
)
# Spaghetti / similar: 400* V/, 2* V/
_STAR_V_SLASH = re.compile(r"\b\d+\s*\*\s*V\s*/", re.IGNORECASE)
# Sachets: 5*1.6G/, 5 * 2G/
_STAR_WEIGHT_SLASH = re.compile(
    r"\b\d+\s*\*\s*\d+(?:\.\d+)?\s*G\s*/",
    re.IGNORECASE,
)
# Multipack hint before a word: 6* VANILLA, 5* SATCHETS, 5* SATS, 6* ASSORTED
_STAR_THEN_WORD = re.compile(
    r"\b\d+\s*\*\s+[A-Z][A-Z0-9]{1,24}\b",
    re.IGNORECASE,
)
# Stray number + slash + next token, e.g. "… 180/ J/SUPER"
_NUM_SLASH = re.compile(r"\b\d{2,4}\s*/\s+(?=[A-Z0-9])", re.IGNORECASE)
# Pack count then weight-slash, e.g. "QUEEN CAKE 6 180/ J/SUPER"
_COUNT_THEN_NUMSLASH = re.compile(r"\b\d{1,2}\s+\d{2,4}\s*/\s+", re.IGNORECASE)
# Trailing small pack count: "BROWN ROLLS 6" (1–2 digits only at end)
_TRAILING_PACK_COUNT = re.compile(r"\s+\d{1,2}\s*$")
# Trailing multipack asterisk only: "MILO 5*", "COFFEE 5*"
_TRAILING_NUM_STAR = re.compile(r"\b\d+\*\s*$", re.IGNORECASE)

# Sold-by-kg suffix: "…/KG", "… / KG", "CAPON/KG"
_PER_KG = re.compile(r"/\s*KG\b", re.IGNORECASE)
# Multipack PK: 4PK, 8PK, 6PK
_NUM_PK = re.compile(r"\b\d+PK\b", re.IGNORECASE)
# Count + S + slash, e.g. 14S/
_NUM_S_SLASH = re.compile(r"\b\d+S/", re.IGNORECASE)
# Count + S as word: 10S, 80S, 6S (after _NUM_S_SLASH so "14S/" is one hit)
_NUM_S_COUNT = re.compile(r"\b\d+S\b", re.IGNORECASE)

# Supermarket shelf / promo codes
_NUM_J_SUPER = re.compile(r"\b\d+\s+J/SUPER\b", re.IGNORECASE)
_J_SUPER = re.compile(r"\bJ/SUPER\b", re.IGNORECASE)

# Empty parentheses: "( )", "()"
_PAREN_EMPTY = re.compile(r"\(\s*\)")
# Cube counts: (6S)
_PAREN_NUM_S = re.compile(r"\(\s*\d+\s*S\s*\)", re.IGNORECASE)
# "CUBE / (6S)" style
_SLASH_BEFORE_PAREN_S = re.compile(r"\s*/\s*\(\s*\d+\s*S\s*\)", re.IGNORECASE)
# Whitespace both sides of / (keeps S/BERRY, T/BAG, G/TOP untouched)
_SPACE_SLASH_SPACE = re.compile(r"\s+/\s+")

_PACK = re.compile(
    r"\b(?:"
    r"PL\s+BTL|PLBTL|P\.?L\.?\s*BTL|"
    r"PET|BTL|TETRA|CAN|PCH|PKT|PACK|CUP|"
    r"PC\b(?!S)|"  # lone PC, not PCS (numbered PCS handled by _PCS)
    r"SATCHETS?|SACHETS?|SATS\b|"
    r"JAR|TUB|BOTTLE|TB|"
    r"TR\b|GLASS|CTN\b|CARTON|POUCH"
    r")\b",
    re.IGNORECASE,
)
_SHELF = re.compile(r"\bL/LIFE\b|\bESL\b|\bUHT\b", re.IGNORECASE)

# Abbreviation / stray dots not part of decimals (e.g. G., ORIG., BISC., "BREAD .")
_PUNCT_DOT = re.compile(r"(?<!\d)\.(?!\d)")
_COMMA = re.compile(r",+")
# Trailing full stops / commas after other cleanup
_TRAILING_DOT_COMMA = re.compile(r"[.,]+\s*$")
# Standalone PL (e.g. soda "PL"); PL BTL is removed via _PACK first in practice
_PL_MARK = re.compile(r"\bPL\b", re.IGNORECASE)

# Export / shelf noise: EOT, E O T, E  O  T
_REMOVE_EOT = re.compile(r"\bE\s*O\s*T\b", re.IGNORECASE)
# Abbreviations → full words (whole token; does not alter CHOCOLATE / BISCUIT)
_CHOC_WORD = re.compile(r"\bCHOC\b")
_BISC_WORD = re.compile(r"\bBISC\b")
# Mill bakers / similar suffixes (longest match first)
_REMOVE_M_BAKERS = re.compile(r"\bM/BAKERS\b", re.IGNORECASE)
_REMOVE_M_BAK = re.compile(r"\bM/BAK\b", re.IGNORECASE)
_REMOVE_M_B = re.compile(r"\bM/B\b", re.IGNORECASE)
# Trailing carton marker (not CTN — word boundary prevents matching inside CTN)
_REMOVE_CT = re.compile(r"\bCT\b", re.IGNORECASE)


def _apply_lexical_normalization(s: str) -> str:
    """Remove POS artefacts; expand CHOC/BISC for matching."""
    s = _REMOVE_EOT.sub(" ", s)
    s = _CHOC_WORD.sub("CHOCOLATE", s)
    s = _BISC_WORD.sub("BISCUIT", s)
    s = _REMOVE_M_BAKERS.sub(" ", s)
    s = _REMOVE_M_BAK.sub(" ", s)
    s = _REMOVE_M_B.sub(" ", s)
    s = _REMOVE_CT.sub(" ", s)
    return s


def normalize_pack_description(raw: str) -> str:
    """
    Remove pack size, multipack counts, and common pack-type tokens.
    Preserves flavour/variant words (APPLE, MANGO, S/BERRY, etc.).
    """
    s = raw.strip().upper()
    for _ in range(18):
        prev = s
        s = _MULTI.sub(" ", s)
        s = _VOL_X_COUNT.sub(" ", s)
        s = _STAR_WEIGHT_SLASH.sub(" ", s)
        s = _STAR_V_SLASH.sub(" ", s)
        s = _STAR_THEN_WORD.sub(" ", s)
        s = _SIZE_UNITS.sub(" ", s)
        s = _SIZE_L.sub(" ", s)
        s = _G_WITH_SLASH.sub(" ", s)
        s = _SIZE_G.sub(" ", s)
        # G##G before _GLUED_SIZE_G so "G14G" is not split into stray "G" + "14G"
        s = _G_NUM_G.sub(" ", s)
        s = _GLUED_SIZE_UNITS.sub(" ", s)
        s = _GLUED_SIZE_L.sub(" ", s)
        s = _GLUED_SIZE_G.sub(" ", s)
        s = _APOST_S.sub(" ", s)
        s = _PCS.sub(" ", s)
        s = _COUNT_THEN_NUMSLASH.sub(" ", s)
        s = _NUM_SLASH.sub(" ", s)
        s = _PAREN_EMPTY.sub(" ", s)
        s = _SLASH_BEFORE_PAREN_S.sub(" ", s)
        s = _PAREN_NUM_S.sub(" ", s)
        s = _PER_KG.sub(" ", s)
        s = _NUM_PK.sub(" ", s)
        s = _NUM_S_SLASH.sub(" ", s)
        s = _NUM_S_COUNT.sub(" ", s)
        s = _NUM_J_SUPER.sub(" ", s)
        s = _J_SUPER.sub(" ", s)
        s = _SPACE_SLASH_SPACE.sub(" ", s)
        s = _PACK.sub(" ", s)
        s = _SHELF.sub(" ", s)
        s = _PUNCT_DOT.sub(" ", s)
        s = _COMMA.sub(" ", s)
        s = _PL_MARK.sub(" ", s)
        s = _apply_lexical_normalization(s)
        s = re.sub(r"\s+", " ", s).strip()
        s = _TRAILING_DOT_COMMA.sub("", s).strip()
        s = _TRAILING_NUM_STAR.sub("", s).strip()
        s = _TRAILING_PACK_COUNT.sub("", s).strip()
        if s == prev:
            break
    s = re.sub(r"\s+", " ", s).strip()
    s = _TRAILING_DOT_COMMA.sub("", s).strip()
    return s
