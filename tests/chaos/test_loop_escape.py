"""KPI-16: loop escape must be 100%. KPI-19 (Tier-3 PENDING) is in test_tier3_pending.py."""

import json
import random
from pathlib import Path
from typing import Any

from contracts.common import CallContext
from harness.executor import ToolExecutionHarness
from harness.loop import MAX_ITERATIONS, Action, Final, LoopState, ReActLoop
from harness.registry import Subagent
from harness.transport import InProcessTransport
from tests.conftest import ctx_model

TOOLS = [
    (Subagent.RETRIEVAL, "hybrid_retrieve"),
    (Subagent.RETRIEVAL, "graph_subgraph"),
    (Subagent.RETRIEVAL, "graph_resolve_anchor"),
    (Subagent.EXECUTION, "workflow_transition"),
    (Subagent.EXECUTION, "session_put_fact"),
    (Subagent.TRIAGE, "hybrid_retrieve"),
    (Subagent.RETRIEVAL, "not_a_tool"),
]


class AdversarialPolicy:
    """Never stops on its own: picks random (sometimes malformed, sometimes repeated) actions."""

    def __init__(self, rng: random.Random, ctx: CallContext) -> None:
        self.rng = rng
        self.ctx = ctx

    def next(self, state: LoopState) -> Action | Final:
        sub, tool = self.rng.choice(TOOLS)
        ctx = self.ctx.model_dump(mode="json")
        choice = self.rng.random()
        args: dict[str, Any]
        if choice < 0.3:
            args = {"ctx": ctx, "query": "x", "top_k_sparse": 1}  # schema failure
        elif choice < 0.5:
            args = {"ctx": ctx, "anchor_note_id": "sku-999", "depth": 1, "token_budget": 100}
        elif choice < 0.7:
            args = {
                "ctx": ctx,
                "entity": "SKU-1234",
                "from_state": "a",
                "to_state": "b",
                "payload": {},
            }
        else:
            args = {"ctx": ctx, "query": "LOT-88392 safety"}
        return Action(sub, tool, args)


async def test_every_adversarial_run_terminates_within_bounds(stack) -> None:  # type: ignore[no-untyped-def]
    rng = random.Random(1234)
    runs = 200
    escaped_or_terminal = 0
    max_seen = 0
    for _ in range(runs):
        c = ctx_model(tier="TIER_3_MUTATE")
        harness = ToolExecutionHarness(InProcessTransport(stack.servers), timeout_s=5.0)
        res = await ReActLoop(harness).run("adversarial", c, AdversarialPolicy(rng, c))
        assert res.iterations <= MAX_ITERATIONS
        assert res.status in {"ESCAPED", "PENDING_APPROVAL", "ABSTAINED", "ANSWERED"}
        max_seen = max(max_seen, res.iterations)
        escaped_or_terminal += 1
    assert escaped_or_terminal == runs  # KPI-16 (indicative: mock policy, local)
    _write(
        "kpi16_loop_escape.json",
        {
            "runs": runs,
            "terminal_or_escaped": escaped_or_terminal,
            "max_iterations_observed": max_seen,
            "max_iterations_bound": MAX_ITERATIONS,
        },
    )


async def test_never_ending_policy_is_escaped(stack) -> None:  # type: ignore[no-untyped-def]
    class Forever:
        def next(self, state: LoopState) -> Action:
            c = ctx_model().model_dump(mode="json")
            return Action(Subagent.RETRIEVAL, "hybrid_retrieve", {"ctx": c, "query": "LOT-88392"})

    res = await ReActLoop(ToolExecutionHarness(InProcessTransport(stack.servers), timeout_s=5.0)).run(
        "loop", ctx_model(), Forever()
    )
    assert res.status == "ESCAPED"
    assert res.iterations == MAX_ITERATIONS


def _write(name: str, data: dict[str, Any]) -> None:
    out = Path(__file__).resolve().parents[2] / "results" / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"evidence_class": "indicative", **data}, indent=2), encoding="utf-8")
