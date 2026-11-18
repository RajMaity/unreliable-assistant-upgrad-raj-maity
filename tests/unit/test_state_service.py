import uuid

import fakeredis
import pytest

from contracts.common import CallContext
from contracts.errors import ErrorCode, ToolError
from contracts.tools import AuditAppendIn, AuditVerifyIn, Fact, HitlDecideIn
from mcp_servers.state_store import Cache, StateService, Store
from tests.conftest import ctx_model


@pytest.fixture
def svc(tmp_path):  # type: ignore[no-untyped-def]
    return StateService(Store(str(tmp_path / "s.sqlite3")), Cache(fakeredis.FakeRedis(decode_responses=True)))


def ctx(**kw: object) -> CallContext:
    return ctx_model(**kw)


def test_cache_is_tenant_scoped(svc: StateService) -> None:
    svc.cache.put("tenant_a", "k" * 64, '{"v": 1}', 300)
    assert svc.cache.get("tenant_a", "k" * 64) == '{"v": 1}'
    assert svc.cache.get("tenant_b", "k" * 64) is None


def test_facts_are_session_and_tenant_scoped(svc: StateService) -> None:
    fact = Fact(
        subject="SKU-099",
        predicate="status",
        object="ok",
        source_chunk_ids=["chk_0123456789abcdef"],
        confidence=0.9,
    )
    svc.put_fact(ctx(), fact)
    assert len(svc.get_facts(ctx(), 10).facts) == 1
    assert svc.get_facts(ctx(tenant="tenant_9"), 10).facts == []
    assert svc.get_facts(ctx(session="sess_zzzzzzzz"), 10).facts == []


def test_idempotency_replays_same_and_conflicts_on_different_args(svc: StateService) -> None:
    key = str(uuid.uuid4())
    c = ctx(tier="TIER_2_COMPUTE", key=key)
    first = svc.idempotent(c, {"a": 1}, lambda: {"fact_id": "x"})
    again = svc.idempotent(c, {"a": 1}, lambda: {"fact_id": "NEW"})
    assert first == again == {"fact_id": "x"}
    with pytest.raises(ToolError) as exc:
        svc.idempotent(c, {"a": 2}, lambda: {})
    assert exc.value.code is ErrorCode.IDEMPOTENCY_CONFLICT


def test_idempotency_requires_uuid4_on_tier2(svc: StateService) -> None:
    c = CallContext(
        tenant_id="tenant_7",
        user_id="user_42",
        session_id="sess_ab12cd34ef",
        idempotency_key=uuid.UUID(int=1, version=1),
        tier="TIER_2_COMPUTE",
    )
    with pytest.raises(ToolError):
        svc.idempotent(c, {}, lambda: {})


def test_approval_pending_then_approved_by_other_user(svc: StateService) -> None:
    requester = ctx(tier="TIER_3_MUTATE")
    pending = svc.request_approval(requester, "workflow_transition", {"entity": "LOT-00044"})
    assert pending["status"] == "PENDING_APPROVAL"
    aid = uuid.UUID(pending["approval_id"])
    approver = ctx(user="user_77")
    with pytest.raises(ToolError) as exc:
        svc.decide(
            ctx(tier="TIER_3_MUTATE"),
            HitlDecideIn(ctx=ctx(), approval_id=aid, decision="APPROVE", reason="self"),
        )
    assert exc.value.code is ErrorCode.RBAC_DENIED
    done = svc.decide(approver, HitlDecideIn(ctx=approver, approval_id=aid, decision="APPROVE", reason="ok"))
    assert done.status == "APPROVED" and done.executed_at is not None


def test_audit_chain_verifies_and_detects_tamper(svc: StateService) -> None:
    c = ctx(tier="TIER_1_READ")
    for i in range(3):
        svc.append_audit(c, AuditAppendIn(ctx=c, event_type="e", payload_redacted={"i": i}))
    assert svc.verify_audit("tenant_7", AuditVerifyIn(from_seq=1, to_seq=3)).valid
    svc.store.execute("UPDATE audit_log SET payload_redacted='{\"i\":99}' WHERE tenant_id='tenant_7' AND seq=2")
    res = svc.verify_audit("tenant_7", AuditVerifyIn(from_seq=1, to_seq=3))
    assert not res.valid and res.broken_at == 2
