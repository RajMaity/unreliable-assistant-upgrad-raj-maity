"""Request pipeline: pre_execution guards → cache (exact + semantic) → triage → ReAct loop
→ post_execution (faithfulness) → cache write → spans. PROPOSED module.

Spans carry stage names and timings only; question text is never placed in spans or logs.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any

import pluggy

from contracts.common import CallContext
from contracts.errors import ErrorCode, ToolError
from harness.executor import ToolExecutionHarness
from harness.loop import MockPolicy, Policy, ReActLoop
from harness.registry import Subagent
from ingress.triage import Route, triage
from mcp_servers.retrieval import Index, embed

CACHE_TTL_BASE_S = 300  # design §1.2: 300 s ± 45 s
SEMANTIC_THRESHOLD = 0.92


def normalise(question: str) -> str:
    return " ".join(question.lower().split())


def cache_key(ctx: CallContext, question: str) -> str:
    """design §1.2: SHA256(tenant_id:user_id:norm_query); the server adds the t:{tenant}: prefix."""
    raw = f"{ctx.tenant_id}:{ctx.user_id}:{normalise(question)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def ttl_seconds() -> int:
    return CACHE_TTL_BASE_S + secrets.randbelow(91) - 45


class Ingress:
    def __init__(
        self, harness: ToolExecutionHarness, pm: pluggy.PluginManager, index: Index, policy: Policy | None = None
    ) -> None:
        self.harness = harness
        self.pm = pm
        self.index = index
        self.loop = ReActLoop(harness)
        self.policy = policy or MockPolicy()

    def _span(self, ctx: CallContext, stage: str, t0: float) -> None:
        self.pm.hook.span(
            event={"stage": stage, "tenant_id": ctx.tenant_id, "ms": round((time.perf_counter() - t0) * 1000, 3)}
        )

    async def handle(self, ctx: CallContext, question: str) -> dict[str, Any]:
        t_all = time.perf_counter()
        # (1) pre_execution guards; fail closed
        t0 = time.perf_counter()
        request: dict[str, Any] = {"tenant_id": ctx.tenant_id, "text": question}
        try:
            self.pm.hook.pre_execution(request=request)
        except ToolError as exc:
            self._span(ctx, "pre_execution", t0)
            return self._blocked(exc.code, exc.message, t_all)
        text = str(request["text"])
        self._span(ctx, "pre_execution", t0)

        # (2) cache: exact then semantic (both tenant-scoped on the server)
        t0 = time.perf_counter()
        vec = embed(self.index, text)
        key = cache_key(ctx, text)
        cached = await self.harness.call(
            Subagent.EXECUTION, "cache_get", ctx, {"ctx": ctx.model_dump(mode="json"), "key": key, "semantic_vec": vec}
        )
        self._span(ctx, "cache", t0)
        if cached.ok and cached.data.get("hit"):
            value = cached.data.get("value") or {}
            return {
                "status": "ANSWERED",
                "answer": value.get("answer", ""),
                "citations": value.get("citations", []),
                "cache_hit": True,
                "cache_tier": cached.data.get("tier"),
                "trace": [],
                "iterations": 0,
                "latency_ms": _ms(t_all),
                "pii_hits": request.get("pii_hits", 0),
                "context_texts": [],
            }

        # (3) triage
        t0 = time.perf_counter()
        route = triage(text)
        self._span(ctx, "triage", t0)
        if route is Route.EMPTY:
            return self._result("ABSTAINED", "Empty question.", (), 0, [], (), request, t_all)

        # (4) ReAct loop (bounded)
        t0 = time.perf_counter()
        loop_res = await self.loop.run(text, ctx, self.policy)
        self._span(ctx, "loop", t0)

        # (5) post_execution (faithfulness proxy), cache write for answers only
        response: dict[str, Any] = {
            "kind": "answer",
            "answer": loop_res.answer,
            "abstain": loop_res.status != "ANSWERED",
            "context_texts": list(loop_res.context),
        }
        self.pm.hook.post_execution(response=response)
        if loop_res.status == "ANSWERED":
            t0 = time.perf_counter()
            await self.harness.call(
                Subagent.EXECUTION,
                "cache_put",
                ctx,
                {
                    "ctx": ctx.model_dump(mode="json"),
                    "key": key,
                    "ttl_s": ttl_seconds(),
                    "value": {"answer": loop_res.answer, "citations": list(loop_res.citations), "semantic_vec": vec},
                },
            )
            self._span(ctx, "cache_put", t0)
        return self._result(
            loop_res.status,
            loop_res.answer,
            loop_res.citations,
            loop_res.iterations,
            loop_res.trace,
            loop_res.context,
            request,
            t_all,
            response=response,
        )

    @staticmethod
    def _blocked(code: ErrorCode, message: str, t_all: float) -> dict[str, Any]:
        return {
            "status": "BLOCKED",
            "code": int(code),
            "code_name": code.name,
            "message": message,
            "answer": "",
            "citations": [],
            "cache_hit": False,
            "trace": [],
            "iterations": 0,
            "latency_ms": _ms(t_all),
            "pii_hits": 0,
        }

    def _result(
        self,
        status: str,
        answer: str,
        citations: tuple[str, ...],
        iterations: int,
        trace: list[dict[str, Any]],
        context: tuple[str, ...],
        request: dict[str, Any],
        t_all: float,
        response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = response or {}
        return {
            "status": status,
            "answer": answer,
            "citations": list(citations),
            "cache_hit": False,
            "cache_tier": None,
            "trace": trace,
            "iterations": iterations,
            "latency_ms": _ms(t_all),
            "pii_hits": request.get("pii_hits", 0),
            "faithfulness": response.get("faithfulness"),
            "faithfulness_ok": response.get("faithfulness_ok"),
            "context_size": len(context),
            "context_texts": list(context),
        }


def _ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000, 3)
