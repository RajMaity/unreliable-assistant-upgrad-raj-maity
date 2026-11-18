"""ReAct state machine with hard bounds (design §1.4: max 5 iterations, 450 ms budget).

Terminal statuses: ANSWERED | ABSTAINED | PENDING_APPROVAL | ESCAPED.
ESCAPED means a bound stopped the loop (iteration cap or time budget).
"""

from __future__ import annotations

import inspect
import os
import re
import time
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol

from contracts.common import CallContext
from harness.executor import ToolExecutionHarness, ToolResult
from harness.registry import Subagent

MAX_ITERATIONS = 5
LLM_BUDGET_MS = 3000.0  # PROPOSED amendment value (see docs/ADR_AMENDMENT_001.md)
TOTAL_BUDGET_MS = 450.0
ENTITY = re.compile(r"\b(SKU-\d{3}|LOT-\d{5})\b", re.IGNORECASE)
TRANSITION_WORDS = ("release", "transition", "approve")


@dataclass(frozen=True)
class Action:
    subagent: Subagent
    tool: str
    args: dict[str, Any]


@dataclass(frozen=True)
class Final:
    answer: str
    abstain: bool
    citations: tuple[str, ...] = ()


@dataclass
class LoopState:
    question: str
    ctx: CallContext
    steps: list[tuple[Action, ToolResult]] = field(default_factory=list)


class Policy(Protocol):
    def next(self, state: LoopState) -> Action | Final | Awaitable[Action | Final]: ...


@dataclass
class LoopResult:
    status: str
    answer: str
    citations: tuple[str, ...]
    iterations: int
    trace: list[dict[str, Any]]
    elapsed_ms: float
    context: tuple[str, ...] = ()


class ReActLoop:
    def __init__(
        self,
        harness: ToolExecutionHarness,
        max_iterations: int = MAX_ITERATIONS,
        budget_ms: float | None = None,
        llm_budget_ms: float | None = None,
    ) -> None:
        self.harness = harness
        self.max_iterations = max_iterations
        # Tool/harness budget: design ADR-004, 450 ms. Policy (LLM) time is NOT charged here.
        self.budget_ms = (
            budget_ms if budget_ms is not None else float(os.environ.get("LOOP_BUDGET_MS", TOTAL_BUDGET_MS))
        )
        # PROPOSED ADR-004 amendment (docs/ADR_AMENDMENT_001.md): separate per-question LLM budget.
        self.llm_budget_ms = (
            llm_budget_ms if llm_budget_ms is not None else float(os.environ.get("LLM_BUDGET_MS", LLM_BUDGET_MS))
        )

    async def run(self, question: str, ctx: CallContext, policy: Policy) -> LoopResult:
        state = LoopState(question=question, ctx=ctx)
        t0 = time.perf_counter()
        policy_ms = 0.0  # time spent inside the policy (LLM calls)

        def result(status: str, answer: str = "", citations: tuple[str, ...] = ()) -> LoopResult:
            trace = [
                {
                    "tool": a.tool,
                    "subagent": a.subagent.value,
                    "status": r.status,
                    "repairs": r.repairs,
                    "approval_id": r.data.get("approval_id"),
                }
                for a, r in state.steps
            ]
            context = tuple(
                c["text"]
                for act, res in state.steps
                if act.tool == "hybrid_retrieve" and res.ok
                for c in res.data.get("chunks", [])
            )
            return LoopResult(
                status,
                answer,
                citations,
                len(state.steps),
                trace,
                (time.perf_counter() - t0) * 1000,
                context,
            )

        def tool_elapsed_ms() -> float:
            return (time.perf_counter() - t0) * 1000 - policy_ms

        for _ in range(self.max_iterations):
            if tool_elapsed_ms() > self.budget_ms:
                return result("ESCAPED", "time budget exhausted")
            if policy_ms > self.llm_budget_ms:
                return result("ESCAPED", "llm budget exhausted")
            p0 = time.perf_counter()
            decision = policy.next(state)
            if inspect.isawaitable(decision):  # async policies (LLM planner)
                decision = await decision
            policy_ms += (time.perf_counter() - p0) * 1000
            if isinstance(decision, Final):
                status = "ABSTAINED" if decision.abstain else "ANSWERED"
                return result(status, decision.answer, decision.citations)
            res = await self.harness.call(decision.subagent, decision.tool, ctx, decision.args)
            state.steps.append((decision, res))
            if res.status == "PENDING_APPROVAL":
                # Tier-3 freeze: the loop never approves on its own (KPI-19).
                return result("PENDING_APPROVAL", "awaiting human approval")
        return result("ESCAPED", "iteration cap reached")


class MockPolicy:
    """Deterministic policy for LLM_BACKEND=mock.

    Transactional intent (entity + transition word) goes straight to the Tier-3 action; it does not
    depend on retrieval. Otherwise: retrieval, then an extractive answer from the top chunk.
    """

    def next(self, state: LoopState) -> Action | Final:
        ctx = state.ctx.model_dump(mode="json")
        q = state.question
        entities = [e.upper() for e in ENTITY.findall(q)]
        transactional = bool(entities) and any(w in q.lower() for w in TRANSITION_WORDS)
        if not state.steps:
            if transactional:
                return self._transition(state, entities[0])
            return Action(Subagent.RETRIEVAL, "hybrid_retrieve", {"ctx": ctx, "query": q})
        last_action, last = state.steps[-1]
        if last_action.tool == "hybrid_retrieve":
            if not last.ok:
                return Final("retrieval unavailable", abstain=True)
            if last.data.get("abstain"):
                return Final("No grounded answer in the knowledge vault.", abstain=True)
            return self._answer(last.data)
        if last_action.tool == "workflow_transition":
            return Final("Change requested; pending human approval.", abstain=False)
        return self._answer(state.steps[0][1].data)

    @staticmethod
    def _transition(state: LoopState, entity: str) -> Action:
        ctx = state.ctx.model_dump(mode="json")
        return Action(
            Subagent.EXECUTION,
            "workflow_transition",
            {
                "ctx": {**ctx, "tier": "TIER_3_MUTATE", "idempotency_key": str(uuid.uuid4())},
                "entity": entity,
                "from_state": "held",
                "to_state": "released",
                "payload": {"requested_in": state.question},
            },
        )

    @staticmethod
    def _answer(retrieval: dict[str, Any]) -> Final:
        chunks = retrieval.get("chunks", [])
        if not chunks:
            return Final("No grounded answer in the knowledge vault.", abstain=True)
        top = chunks[0]
        return Final(top["text"], abstain=False, citations=tuple(c["chunk_id"] for c in chunks))
