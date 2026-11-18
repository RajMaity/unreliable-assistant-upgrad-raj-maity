"""Deterministic regex repair (design.md §1.2 'Regex auto-repair rules').

Pure syntactic canonicalisation only. Never invents data: anything that would
need a guess returns None (-32003 VALIDATION_FAILED at the harness layer).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# U+200B, U+200C, U+200D, U+2060, U+FEFF
_ZERO_WIDTH = dict.fromkeys([0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF], None)

# Explicit [0-9] (not \d) so non-ASCII digits never sneak through repair.
_SKU_CANON = re.compile(r"^SKU-[0-9]{3}$")
_LOT_CANON = re.compile(r"^LOT-[0-9]{5}$")
_TENANT_CANON = re.compile(r"^tenant_[A-Za-z0-9]{1,32}$")
_R1 = re.compile(r"^\s*sku[\s_\-]?0*([0-9]{1,3})\s*$", re.IGNORECASE)
_R2 = re.compile(r"^\s*lot[\s_\-#:]*0*([0-9]{1,5})\s*$", re.IGNORECASE)
_R4 = re.compile(r"^tenant_(.*)$", re.IGNORECASE)


@dataclass(frozen=True)
class Repaired:
    value: str
    rule: str | None  # None = already canonical; otherwise "R1".."R4"


def _nfkc_clean(raw: str) -> tuple[str, bool]:
    """R3: NFKC normalise, strip zero-width chars and surrounding whitespace."""
    cleaned = unicodedata.normalize("NFKC", raw).translate(_ZERO_WIDTH).strip()
    return cleaned, cleaned != raw


def _repair_id(raw: str, canon: re.Pattern[str], pattern: re.Pattern[str], fmt: str) -> Repaired | None:
    value, r3 = _nfkc_clean(raw)
    if canon.fullmatch(value):
        return Repaired(value, "R3" if r3 else None)
    m = pattern.match(value)
    if m is None:
        return None
    candidate = fmt.format(int(m.group(1)))
    return Repaired(candidate, "R1" if pattern is _R1 else "R2")


def repair_sku(raw: str) -> Repaired | None:
    return _repair_id(raw, _SKU_CANON, _R1, "SKU-{:03d}")


def repair_lot(raw: str) -> Repaired | None:
    return _repair_id(raw, _LOT_CANON, _R2, "LOT-{:05d}")


def repair_tenant_id(raw: str) -> Repaired | None:
    """R4: lower-case the 'tenant_' prefix. Never changes the tenant body."""
    value, r3 = _nfkc_clean(raw)
    rule: str | None = None
    m = _R4.match(value)
    if m is not None and value[:7] != "tenant_":
        value = "tenant_" + m.group(1)
        rule = "R4"
    elif r3:
        rule = "R3"
    if not _TENANT_CANON.fullmatch(value):
        return None
    return Repaired(value, rule)
