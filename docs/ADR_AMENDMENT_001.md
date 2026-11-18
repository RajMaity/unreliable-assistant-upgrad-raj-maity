# ADR-004 amendment 001: separate LLM budget from the tool loop budget

**Status: PROPOSED. Not yet approved. `design.md` is unchanged; this document is the amendment request.**

## Context
- ADR-004 bounds the ReAct loop to 450 ms total. The LLM planner and generator run inside that loop.
- Measured on this machine against OpenRouter (`anthropic/claude-haiku-4.5`), one LLM call takes about 1–2 s.
  With the 450 ms budget every live question escapes after one LLM decision (verified against the API).
- The design's LLM step budget (row 11) is 190 ms p95 TTFT, which a remote API cannot meet.

## Decision (proposed)
1. **Tool budget unchanged:** 450 ms per question for harness and tool steps (retrieval, graph, state store).
   LLM time is not charged to this budget.
2. **New LLM budget:** 3000 ms per question for all policy/LLM time (`LLM_BUDGET_MS`). Exceeding it escapes the loop
   with `llm budget exhausted`.
3. **Iteration cap unchanged:** 5 ReAct iterations; breaker and HITL rules unchanged.
4. **Latency KPIs for the LLM path (proposed):**

| KPI | Contract | Proposed for the LLM path |
|---|---|---|
| KPI-31 p50 end-to-end | ≤ 240 ms | ≤ 2500 ms |
| KPI-32 p95 end-to-end | ≤ 450 ms | ≤ 4500 ms |
| KPI-33 p99 end-to-end | ≤ 590 ms | ≤ 6000 ms |
| KPI-25 LLM time to first token p95 | ≤ 180 ms | ≤ 3000 ms full response until streaming is measured |

The contract values stay in force for the tool-only paths (cache hit, abstention short-circuit, tool-only answers).

## Consequences
- The LLM answers within the latency bounds above. Answers are no longer capped at 450 ms.
- The latency SLA for LLM-generated answers is weaker than the contract. This is the trade-off being accepted.
- Cache-hit (≈53 ms) and abstention (<90 ms) paths are unchanged.
- KPI-17 (steps) and KPI-16 (loop escape) are unchanged: the iteration cap still bounds every run.
- Evidence must be labelled: live numbers are `measured` against a remote model; the judge is the same model family.

## Verification plan
- `tests/unit/test_budget_split.py`: policy time is not charged to the tool budget, and the LLM budget still bounds it.
- Live evaluation (`RUN_LIVE_EVAL=1 pytest tests/eval -m live`) at default budgets, with no override.
- Scorecard shows both the contract target and the proposed amended target for KPI-25 and KPI-31–33.

## Approval
- [ ] Approved as proposed
- [ ] Approved with changes: ______
- [ ] Rejected (keep the contract; choose option 1–3 from the options memo)
