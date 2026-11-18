"""LIVE evaluation through the real OpenRouter model (planner + generation + LLM judge).

Skipped unless OPENROUTER_API_KEY is set. Writes results/eval_llm_golden.json.
Evidence class: `measured` for generation (real model, real local stack). The judge is an LLM
judge, not the RAGAS library, so judged metrics are reported as LLM-judge scores.
Run: OPENROUTER_API_KEY=... RUN_LIVE_EVAL=1 LOOP_BUDGET_MS=20000 pytest tests/eval -m live -q
"""

import json
import os
import statistics
import time
from pathlib import Path

import pytest

from harness.executor import ToolExecutionHarness
from harness.llm_policy import LLMPolicy
from harness.transport import InProcessTransport
from ingress.pipeline import Ingress
from llm import judge
from llm.client import OpenRouterClient
from mcp_servers.retrieval import Index
from plugins.loader import build_manager
from tests.conftest import ctx_model

GOLDEN = json.loads((Path(__file__).parent / "data" / "golden.json").read_text(encoding="utf-8"))
OUT = Path(__file__).resolve().parents[2] / "results" / "eval_llm_golden.json"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (os.environ.get("OPENROUTER_API_KEY") and os.environ.get("RUN_LIVE_EVAL") == "1"),
        reason="live eval is opt-in: set OPENROUTER_API_KEY and RUN_LIVE_EVAL=1",
    ),
]


class CountingClient:
    def __init__(self, inner: OpenRouterClient) -> None:
        self.inner = inner
        self.calls = 0
        self.latencies: list[float] = []

    async def chat(self, messages, *, max_tokens: int = 600) -> str:  # type: ignore[no-untyped-def]
        self.calls += 1
        t0 = time.perf_counter()
        try:
            return await self.inner.chat(messages, max_tokens=max_tokens)
        finally:
            self.latencies.append((time.perf_counter() - t0) * 1000)


def _pct(xs: list[float], p: int) -> float:
    s = sorted(xs)
    return round(s[min(len(s) - 1, max(0, round(p / 100 * len(s)) - 1))], 1) if s else 0.0


async def test_llm_golden_live(stack, vault) -> None:  # type: ignore[no-untyped-def]
    counting = CountingClient(
        OpenRouterClient(
            os.environ["OPENROUTER_API_KEY"], os.environ.get("OPENROUTER_MODEL", "anthropic/claude-haiku-4.5")
        )
    )
    policy = LLMPolicy(counting)
    pm = build_manager()
    harness = ToolExecutionHarness(InProcessTransport(stack.servers), timeout_s=30.0, pm=pm)
    ing = Ingress(harness, pm, Index.build(vault), policy=policy)
    ctx = ctx_model()

    rows = []
    for g in GOLDEN:
        t0 = time.perf_counter()
        res = await ing.handle(ctx, g["q"])
        wall = (time.perf_counter() - t0) * 1000
        faith = rel = None
        if res["status"] == "ANSWERED":
            faith = await judge.faithfulness(counting, res["answer"], res["context_texts"])
            rel = await judge.relevancy(counting, g["q"], res["answer"])
        rows.append(
            {
                "q": g["q"],
                "answerable": g["answerable"],
                "status": res["status"],
                "answer": res["answer"][:300],
                "citations": res["citations"],
                "wall_ms": round(wall, 1),
                "iterations": res["iterations"],
                "faithfulness_judge": faith,
                "relevancy_judge": rel,
            }
        )

    ans = [r for r in rows if r["answerable"]]
    unans = [r for r in rows if not r["answerable"]]
    answered = [r for r in ans if r["status"] == "ANSWERED"]
    faith_vals = [r["faithfulness_judge"] for r in answered if r["faithfulness_judge"] is not None]
    rel_vals = [r["relevancy_judge"] for r in answered if r["relevancy_judge"] is not None]
    report = {
        "evidence_class": "measured",
        "judge": "LLM-as-judge (same model family), not the RAGAS library",
        "model": counting.inner.model,
        "llm_calls": counting.calls,
        "KPI-20_faithfulness_pass_rate_llm_judge": round(sum(f >= 0.92 for f in faith_vals) / len(faith_vals), 4)
        if faith_vals
        else None,
        "KPI-20_mean_faithfulness_llm_judge": round(statistics.fmean(faith_vals), 4) if faith_vals else None,
        "KPI-21_answer_relevancy_llm_judge": round(statistics.fmean(rel_vals), 4) if rel_vals else None,
        "KPI-17_mean_react_steps": round(statistics.fmean(r["iterations"] for r in rows), 3),
        "KPI-23_abstention_accuracy": round(
            (sum(r["status"] == "ANSWERED" for r in ans) + sum(r["status"] == "ABSTAINED" for r in unans)) / len(rows),
            4,
        ),
        "KPI-25_llm_call_p95_ms_full_response_not_ttft": _pct(counting.latencies, 95),
        "e2e_wall_p50_ms": _pct([r["wall_ms"] for r in rows], 50),
        "e2e_wall_p95_ms": _pct([r["wall_ms"] for r in rows], 95),
        "e2e_wall_p99_ms": _pct([r["wall_ms"] for r in rows], 99),
        "loop_budget_ms": float(os.environ.get("LOOP_BUDGET_MS", 450)),
        "rows": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
