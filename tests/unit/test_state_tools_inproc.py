import uuid

import fakeredis
import pytest

from contracts.errors import ErrorCode, ToolError
from contracts.tools import (
    AuditAppendIn,
    AuditVerifyIn,
    CacheGetIn,
    CachePutIn,
    Fact,
    FormSubmitIn,
    HitlDecideIn,
    SessionGetFactsIn,
    SessionPutFactIn,
    WorkflowTransitionIn,
)
from mcp_servers.state_store import Cache, StateService, Store, build_server
from tests.conftest import ctx_model
from tests.unit.test_servers_inproc import fake_ctx, tool_fn

KEY = "d" * 64


@pytest.fixture
def server(tmp_path):  # type: ignore[no-untyped-def]
    svc = StateService(Store(str(tmp_path / "s.sqlite3")), Cache(fakeredis.FakeRedis(decode_responses=True)))
    return build_server(svc), svc


def test_cache_tools(server) -> None:  # type: ignore[no-untyped-def]
    mcp, _ = server
    put = tool_fn(mcp, "cache_put")(
        payload=CachePutIn(ctx=ctx_model(), key=KEY, value={"answer": "42"}, ttl_s=300),
        ctx=fake_ctx(),
    )
    assert put.stored
    hit = tool_fn(mcp, "cache_get")(payload=CacheGetIn(ctx=ctx_model(), key=KEY), ctx=fake_ctx())
    assert hit.hit and hit.tier == "exact"
    miss = tool_fn(mcp, "cache_get")(payload=CacheGetIn(ctx=ctx_model(), key="e" * 64), ctx=fake_ctx())
    assert not miss.hit
    sem_put = tool_fn(mcp, "cache_put")(
        payload=CachePutIn(
            ctx=ctx_model(),
            key="f" * 64,
            value={"semantic_vec": [1.0, 0.0, 0.0], "answer": "S"},
            ttl_s=300,
        ),
        ctx=fake_ctx(),
    )
    assert sem_put.stored
    sem = tool_fn(mcp, "cache_get")(
        payload=CacheGetIn(ctx=ctx_model(), key="0" * 64, semantic_vec=[0.99, 0.05, 0.0]),
        ctx=fake_ctx(),
    )
    assert sem.hit and sem.tier == "semantic" and sem.value == {"semantic_vec": [1.0, 0.0, 0.0], "answer": "S"}
    no_sem = tool_fn(mcp, "cache_get")(
        payload=CacheGetIn(ctx=ctx_model(), key="0" * 64, semantic_vec=[0.0, 0.0, 1.0]),
        ctx=fake_ctx(),
    )
    assert not no_sem.hit
    other = tool_fn(mcp, "cache_get")(
        payload=CacheGetIn(ctx=ctx_model(tenant="tenant_9"), key=KEY), ctx=fake_ctx("tenant_9")
    )
    assert not other.hit


def test_fact_tools(server) -> None:  # type: ignore[no-untyped-def]
    mcp, _ = server
    fact = Fact(
        subject="SKU-099",
        predicate="status",
        object="ok",
        source_chunk_ids=["chk_0123456789abcdef"],
        confidence=0.8,
    )
    c2 = ctx_model(tier="TIER_2_COMPUTE")
    fid = tool_fn(mcp, "session_put_fact")(payload=SessionPutFactIn(ctx=c2, fact=fact), ctx=fake_ctx())
    again = tool_fn(mcp, "session_put_fact")(payload=SessionPutFactIn(ctx=c2, fact=fact), ctx=fake_ctx())
    assert fid.fact_id == again.fact_id  # idempotent replay
    facts = tool_fn(mcp, "session_get_facts")(payload=SessionGetFactsIn(ctx=ctx_model(), limit=10), ctx=fake_ctx())
    assert len(facts.facts) == 1


def test_tier3_tools_and_hitl(server) -> None:  # type: ignore[no-untyped-def]
    mcp, _ = server
    c3 = ctx_model(tier="TIER_3_MUTATE")
    wt = tool_fn(mcp, "workflow_transition")(
        payload=WorkflowTransitionIn(ctx=c3, entity="LOT-00044", from_state="held", to_state="released", payload={}),
        ctx=fake_ctx(),
    )
    assert wt.status == "PENDING_APPROVAL"
    fs = tool_fn(mcp, "form_submit")(
        payload=FormSubmitIn(ctx=ctx_model(tier="TIER_3_MUTATE"), form_id="F-1", fields={"qty": 3}),
        ctx=fake_ctx(),
    )
    assert fs.status == "PENDING_APPROVAL"
    approver = ctx_model(user="user_77", tier="TIER_3_MUTATE")
    done = tool_fn(mcp, "hitl_decide")(
        payload=HitlDecideIn(ctx=approver, approval_id=wt.approval_id, decision="APPROVE", reason="ok"),
        ctx=fake_ctx(),
    )
    assert done.status == "APPROVED"


def test_idempotency_conflict_over_tool(server) -> None:  # type: ignore[no-untyped-def]
    mcp, _ = server
    key = str(uuid.uuid4())
    c = ctx_model(tier="TIER_3_MUTATE", key=key)
    tool_fn(mcp, "form_submit")(payload=FormSubmitIn(ctx=c, form_id="F-1", fields={}), ctx=fake_ctx())
    with pytest.raises(ToolError) as exc:
        tool_fn(mcp, "form_submit")(payload=FormSubmitIn(ctx=c, form_id="F-2", fields={}), ctx=fake_ctx())
    assert exc.value.code is ErrorCode.IDEMPOTENCY_CONFLICT


def test_audit_tools(server) -> None:  # type: ignore[no-untyped-def]
    mcp, _ = server
    c = ctx_model(tier="TIER_1_READ")
    a = tool_fn(mcp, "audit_append")(
        payload=AuditAppendIn(ctx=c, event_type="query", payload_redacted={"n": 1}), ctx=fake_ctx()
    )
    assert a.seq == 1
    v = tool_fn(mcp, "audit_verify")(payload=AuditVerifyIn(from_seq=1, to_seq=1), ctx=fake_ctx())
    assert v.valid
    with pytest.raises(ToolError):
        tool_fn(mcp, "audit_verify")(payload=AuditVerifyIn(from_seq=1, to_seq=1), ctx=fake_ctx(None))
