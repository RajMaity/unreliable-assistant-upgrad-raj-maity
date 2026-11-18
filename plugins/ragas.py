"""ragas_auditor (POST, priority 10): faithfulness proxy with a 0.92 threshold.

PoC proxy: share of answer tokens that appear in the cited context. Real RAGAS
runs in tests/eval (P5). Results are labelled indicative.
"""

from __future__ import annotations

from typing import Any

from mcp_servers.retrieval import tokenize
from plugins.hookspecs import hookimpl

FAITHFULNESS_MIN = 0.92


def faithfulness_score(answer: str, context_texts: list[str]) -> float:
    ans = tokenize(answer)
    ctx_tokens = set(tokenize(" ".join(context_texts)))
    return round((sum(t in ctx_tokens for t in ans) / len(ans)) if ans else 0.0, 6)


class RagasAuditor:
    @hookimpl
    def post_execution(self, response: dict[str, Any]) -> None:
        if response.get("kind") != "answer" or response.get("abstain"):
            return
        score = faithfulness_score(str(response.get("answer", "")), list(response.get("context_texts", [])))
        response["faithfulness"] = score
        response["faithfulness_ok"] = score >= FAITHFULNESS_MIN
