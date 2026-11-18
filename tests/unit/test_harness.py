import uuid

import pytest

from contracts.common import CallContext
from contracts.errors import ErrorCode
from harness.executor import ToolExecutionHarness
from harness.registry import Subagent
from harness.transport import InProcessTransport
from tests.conftest import ctx_model


@pytest.fixture
def harness(stack):  # type: ignore[no-untyped-def]
    return ToolExecutionHarness(InProcessTransport(stack.servers), timeout_s=5.0)


def ctx(tier: str = "TIER_1_READ", **kw: object) -> CallContext:
    return ctx_model(tier=tier, **kw)  # type: ignore[arg-type]


def j(c: CallContext) -> dict:  # type: ignore[type-arg]
    return c.model_dump(mode="json")


async def test_valid_read_succeeds(harness) -> None:  # type: ignore[no-untyped-def]
    c = ctx()
    r = await harness.call(Subagent.RETRIEVAL, "hybrid_retrieve", c, {"ctx": j(c), "query": "LOT-88392 safety"})
    assert r.ok and r.data["chunks"]


async def test_sku_is_repaired_and_recorded(harness) -> None:  # type: ignore[no-untyped-def]
    # workflow_transition.entity is typed SKU|LOT, so 'sku 99' fails validation and is repaired.
    # (ResolveAnchorIn.entities is list[SKU|LOT|str]; a str member accepts 'sku99' unchanged.)
    c = ctx(tier="TIER_3_MUTATE")
    r = await harness.call(
        Subagent.EXECUTION,
        "workflow_transition",
        c,
        {
            "ctx": j(c),
            "entity": "sku 99",
            "from_state": "held",
            "to_state": "released",
            "payload": {},
        },
    )
    assert r.code is ErrorCode.HITL_PENDING
    assert r.repairs == ["R1:entity"]
    assert r.data["status"] == "PENDING_APPROVAL"
    assert harness.stats.repaired_ok == 1


async def test_unrepairable_sku_rejected(harness) -> None:  # type: ignore[no-untyped-def]
    c = ctx(tier="TIER_3_MUTATE")
    r = await harness.call(
        Subagent.EXECUTION,
        "workflow_transition",
        c,
        {
            "ctx": j(c),
            "entity": "SKU-1234",
            "from_state": "held",
            "to_state": "released",
            "payload": {},
        },
    )
    assert r.code is ErrorCode.VALIDATION_FAILED


async def test_schema_failure_without_repair_is_invalid_params(harness) -> None:  # type: ignore[no-untyped-def]
    c = ctx()
    r = await harness.call(Subagent.RETRIEVAL, "hybrid_retrieve", c, {"ctx": j(c), "query": "x", "top_k_sparse": 1})
    assert r.code is ErrorCode.INVALID_PARAMS


async def test_tenant_mismatch_rejected(harness) -> None:  # type: ignore[no-untyped-def]
    c = ctx()
    other = ctx_model(tenant="tenant_9")
    r = await harness.call(Subagent.RETRIEVAL, "hybrid_retrieve", c, {"ctx": j(other), "query": "x"})
    assert r.code is ErrorCode.TENANT_MISMATCH


async def test_rbac_subagent_binding(harness) -> None:  # type: ignore[no-untyped-def]
    c = ctx(tier="TIER_3_MUTATE")
    r = await harness.call(
        Subagent.RETRIEVAL,
        "workflow_transition",
        c,
        {"ctx": j(c), "entity": "LOT-00044", "from_state": "a", "to_state": "b", "payload": {}},
    )
    assert r.code is ErrorCode.RBAC_DENIED
    r2 = await harness.call(Subagent.TRIAGE, "hybrid_retrieve", c, {"ctx": j(c), "query": "x"})
    assert r2.code is ErrorCode.RBAC_DENIED


async def test_rbac_tier_insufficient(harness) -> None:  # type: ignore[no-untyped-def]
    c = ctx(tier="TIER_1_READ", key=str(uuid.uuid4()))
    r = await harness.call(
        Subagent.EXECUTION,
        "session_put_fact",
        c,
        {
            "ctx": j(c),
            "fact": {
                "subject": "a",
                "predicate": "b",
                "object": "c",
                "source_chunk_ids": [],
                "confidence": 0.5,
            },
        },
    )
    assert r.code is ErrorCode.RBAC_DENIED


async def test_tier2_requires_uuid4(harness) -> None:  # type: ignore[no-untyped-def]
    from contracts.common import CallContext as CallCtx

    c = CallCtx(
        tenant_id="tenant_7",
        user_id="user_42",
        session_id="sess_ab12cd34ef",
        idempotency_key=uuid.UUID(int=1, version=1),
        tier="TIER_2_COMPUTE",
    )
    r = await harness.call(
        Subagent.EXECUTION,
        "session_put_fact",
        c,
        {
            "ctx": j(c),
            "fact": {
                "subject": "a",
                "predicate": "b",
                "object": "c",
                "source_chunk_ids": [],
                "confidence": 0.5,
            },
        },
    )
    assert r.code is ErrorCode.VALIDATION_FAILED


async def test_tier3_is_pending_approval(harness) -> None:  # type: ignore[no-untyped-def]
    c = ctx(tier="TIER_3_MUTATE")
    r = await harness.call(
        Subagent.EXECUTION,
        "workflow_transition",
        c,
        {
            "ctx": j(c),
            "entity": "LOT-00044",
            "from_state": "held",
            "to_state": "released",
            "payload": {},
        },
    )
    assert r.code is ErrorCode.HITL_PENDING
    assert r.status == "PENDING_APPROVAL"
    assert "approval_id" in r.data


async def test_breaker_opens_after_two_identical_failures(harness) -> None:  # type: ignore[no-untyped-def]
    c = ctx()
    args = {"ctx": j(c), "anchor_note_id": "sku-999", "depth": 1, "token_budget": 100}
    first = await harness.call(Subagent.RETRIEVAL, "graph_subgraph", c, args)
    second = await harness.call(Subagent.RETRIEVAL, "graph_subgraph", c, args)
    third = await harness.call(Subagent.RETRIEVAL, "graph_subgraph", c, args)
    assert first.code is ErrorCode.NOT_FOUND
    assert second.code is ErrorCode.NOT_FOUND
    assert third.code is ErrorCode.BREAKER_OPEN


async def test_unknown_tool(harness) -> None:  # type: ignore[no-untyped-def]
    r = await harness.call(Subagent.RETRIEVAL, "rm_rf", ctx(), {})
    assert r.code is ErrorCode.METHOD_NOT_FOUND


async def test_timeout_maps_to_tool_timeout(stack) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    class Slow:
        async def call(self, tool, args, meta):  # type: ignore[no-untyped-def]
            await asyncio.sleep(1)
            return {}

        async def close(self) -> None:
            return None

    h = ToolExecutionHarness(Slow(), timeout_s=0.01)  # type: ignore[arg-type]
    c = ctx()
    r = await h.call(Subagent.RETRIEVAL, "hybrid_retrieve", c, {"ctx": j(c), "query": "x"})
    assert r.code is ErrorCode.TIMEOUT
