"""Triage (subagent TRIAGE: no tools). Routes a question before any retrieval."""

from __future__ import annotations

import re
from enum import StrEnum

from mcp_servers.retrieval import tokenize

TRANSACTIONAL_WORDS = ("release", "transition", "quarantine", "approve", "hold")
ENTITY = re.compile(r"\b(?:SKU-\d{3}|LOT-\d{5})\b", re.IGNORECASE)


class Route(StrEnum):
    RETRIEVAL = "retrieval"
    TRANSACTIONAL = "transactional"
    EMPTY = "empty"


def triage(question: str) -> Route:
    if not tokenize(question):
        return Route.EMPTY
    lowered = question.lower()
    if ENTITY.search(question) and any(w in lowered for w in TRANSACTIONAL_WORDS):
        return Route.TRANSACTIONAL
    return Route.RETRIEVAL
