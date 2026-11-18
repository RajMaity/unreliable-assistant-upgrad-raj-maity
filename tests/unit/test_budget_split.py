"""PROPOSED ADR-004 amendment: LLM time is charged to its own budget, not the 450 ms tool budget."""

import asyncio

from harness.executor import ToolExecutionHarness
from harness.loop import Action, Final, LoopState, ReActLoop
from harness.registry import Subagent
from harness.transport import InProcessTransport
from tests.conftest import ctx_model


class SlowFinal:
    """Policy that takes `delay_s` per decision (simulates an LLM call) and then answers."""

    def __init__(self, delay_s: float, steps_before_final: int = 0) -> None:
        self.delay_s = delay_s
        self.left = steps_before_final

    async def next(self, state: LoopState) -> Action | Final:
        await asyncio.sleep(self.delay_s)
        if self.left > 0:
            self.left -= 1
            c = ctx_model().model_dump(mode="json")
            return Action(Subagent.RETRIEVAL, "hybrid_retrieve", {"ctx": c, "query": "LOT-88392 safety"})
        return Final("done", abstain=False)


def _loop(stack, **kw):  # type: ignore[no-untyped-def]
    return ReActLoop(ToolExecutionHarness(InProcessTransport(stack.servers), timeout_s=5.0), **kw)


async def test_slow_policy_does_not_exhaust_tool_budget(stack) -> None:  # type: ignore[no-untyped-def]
    # 0.6 s per LLM decision is above the 450 ms tool budget, but it is policy time, not tool time.
    res = await _loop(stack, budget_ms=450, llm_budget_ms=5000).run(
        "q", ctx_model(), SlowFinal(delay_s=0.6, steps_before_final=1)
    )
    assert res.status == "ANSWERED"


async def test_llm_budget_still_bounds_policy_time(stack) -> None:  # type: ignore[no-untyped-def]
    res = await _loop(stack, budget_ms=450, llm_budget_ms=300).run(
        "q", ctx_model(), SlowFinal(delay_s=0.2, steps_before_final=5)
    )
    assert res.status == "ESCAPED"
    assert res.answer == "llm budget exhausted"


async def test_tool_time_is_still_bounded(stack) -> None:  # type: ignore[no-untyped-def]
    class ToolHeavy:
        async def next(self, state: LoopState) -> Action:
            # Tool calls stay fast; the loop must still stop on the iteration cap.
            c = ctx_model().model_dump(mode="json")
            return Action(Subagent.RETRIEVAL, "hybrid_retrieve", {"ctx": c, "query": "LOT-88392"})

    res = await _loop(stack, budget_ms=450, llm_budget_ms=3000).run("q", ctx_model(), ToolHeavy())
    assert res.status == "ESCAPED"
    assert res.iterations <= 5
