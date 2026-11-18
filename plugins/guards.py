"""Ingress guards (pre_execution): PII redaction and injection shield. Both fail closed."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from contracts.errors import ErrorCode, ToolError
from plugins.hookspecs import hookimpl

PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("phone", re.compile(r"\+?\d[\d -]{8,14}\d")),
)

INJECTION = re.compile(
    r"ignore (?:all |the |any )?(?:previous|prior|above) (?:instructions|prompts?)"
    r"|disregard (?:all |the |any )?(?:previous|prior|above)? ?(?:instructions|rules|prompts?)"
    r"|reveal (?:your |the )?(?:system )?prompt"
    r"|you are now\b|developer mode|jailbreak|act as (?:an? )?(?:unrestricted|dan)",
    re.IGNORECASE,
)


_ZERO_WIDTH = dict.fromkeys([0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF], None)


def normalise(text: str) -> str:
    """Same canonicalisation as the repair layer (R3): NFKC, zero-width strip, whitespace collapse."""
    return " ".join(unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH).split())


def redact(text: str) -> tuple[str, int]:
    hits = 0
    for label, pat in PII_PATTERNS:
        text, n = pat.subn(f"[{label.upper()}_REDACTED]", text)
        hits += n
    return text, hits


class PiiRedactor:
    """PRE priority 10. Fail closed: any internal error becomes PII_BLOCKED."""

    @hookimpl
    def pre_execution(self, request: dict[str, Any]) -> None:
        try:
            cleaned, hits = redact(str(request["text"]))
        except Exception as exc:  # fail closed
            raise ToolError(ErrorCode.PII_BLOCKED, f"pii_redactor failed: {exc}") from exc
        request["text"] = cleaned
        request["pii_hits"] = hits


class InjectionShield:
    """PRE priority 20. Fail closed: any internal error becomes INJECTION_BLOCKED."""

    @hookimpl
    def pre_execution(self, request: dict[str, Any]) -> None:
        try:
            matched = INJECTION.search(normalise(str(request["text"]))) is not None
        except Exception as exc:  # fail closed
            raise ToolError(ErrorCode.INJECTION_BLOCKED, f"injection_shield failed: {exc}") from exc
        if matched:
            raise ToolError(ErrorCode.INJECTION_BLOCKED, "prompt-injection pattern detected")
