"""IR and proxy metrics for the golden set. All results are INDICATIVE (mock backends)."""

from __future__ import annotations

import math


def precision_at_k(relevant_flags: list[bool], k: int) -> float:
    top = relevant_flags[:k]
    return sum(top) / k if k else 0.0


def recall_at_k(relevant_flags: list[bool], total_relevant: int, k: int) -> float:
    if total_relevant == 0:
        return 1.0
    return min(sum(relevant_flags[:k]), total_relevant) / total_relevant


def mrr_at_k(relevant_flags: list[bool], k: int) -> float:
    for i, rel in enumerate(relevant_flags[:k], start=1):
        if rel:
            return 1.0 / i
    return 0.0


def ndcg_at_k(relevant_flags: list[bool], total_relevant: int, k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 1) for i, rel in enumerate(relevant_flags[:k], start=1) if rel)
    ideal_hits = min(total_relevant, k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg else 0.0


def entity_recall(expected: list[str], text: str) -> float:
    if not expected:
        return 1.0
    return sum(e.upper() in text.upper() for e in expected) / len(expected)


def token_overlap(question: str, answer: str) -> float:
    """Answer-relevancy PROXY (not RAGAS): share of question content tokens found in the answer."""
    from mcp_servers.retrieval import tokenize

    stop = {
        "what",
        "is",
        "the",
        "a",
        "an",
        "of",
        "for",
        "to",
        "in",
        "does",
        "how",
        "when",
        "which",
        "who",
        "do",
        "i",
        "are",
        "be",
        "on",
        "with",
        "from",
        "and",
        "or",
        "it",
        "this",
        "that",
        "if",
    }
    q = [t for t in tokenize(question) if t not in stop]
    if not q:
        return 0.0
    a = set(tokenize(answer))
    return sum(t in a for t in q) / len(q)
