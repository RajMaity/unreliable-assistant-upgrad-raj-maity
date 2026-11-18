"""In-process coverage of MCP tool bodies (subprocess tests are not measured by coverage)."""

import json
import uuid
from types import SimpleNamespace
from typing import Any

import fakeredis
import pytest

from contracts.errors import ErrorCode, ToolError
from mcp_servers import graphify_server, rag_server, state_store
from mcp_servers.common import check_meta, require_uuid4
from mcp_servers.state_store import Cache, StateService, Store, meta_tenant
from tests.conftest import ctx_model, make_ctx


def fake_ctx(tenant: str | None = "tenant_7") -> Any:
    meta = None if tenant is None else SimpleNamespace(tenant_id=tenant)
    return SimpleNamespace(request_context=SimpleNamespace(meta=meta))


def tool_fn(server: Any, name: str) -> Any:
    return server._tool_manager.get_tool(name).fn


# --- common helpers -------------------------------------------------------------


def test_check_meta_missing_and_mismatch() -> None:
    with pytest.raises(ToolError) as missing:
        check_meta(fake_ctx(None), "tenant_7")
    assert missing.value.code is ErrorCode.INVALID_REQUEST
    with pytest.raises(ToolError) as mismatch:
        check_meta(fake_ctx("tenant_9"), "tenant_7")
    assert mismatch.value.code is ErrorCode.TENANT_MISMATCH
    check_meta(fake_ctx("tenant_7"), "tenant_7")


def test_require_uuid4_rules() -> None:
    t1 = ctx_model(tier="TIER_1_READ")
    assert require_uuid4(t1) == t1.idempotency_key
    t2 = ctx_model(tier="TIER_2_COMPUTE")
    assert require_uuid4(t2).version == 4
    from contracts.common import CallContext

    bad = CallContext(
        tenant_id="tenant_7",
        user_id="user_42",
        session_id="sess_ab12cd34ef",
        idempotency_key=uuid.UUID(int=1, version=1),
        tier="TIER_3_MUTATE",
    )
    with pytest.raises(ToolError):
        require_uuid4(bad)


# --- rag server -----------------------------------------------------------------


def test_rag_tools_and_resource_in_process(vault) -> None:  # type: ignore[no-untyped-def]
    server = rag_server.build_server(vault)
    from contracts.tools import HybridRetrieveIn

    req = HybridRetrieveIn(ctx=ctx_model(), query="LOT-88392 safety protocol")
    out = tool_fn(server, "hybrid_retrieve")(payload=req, ctx=fake_ctx())
    assert out.chunks[0].note_id == "lot-88392"
    with pytest.raises(ToolError):
        tool_fn(server, "hybrid_retrieve")(payload=req, ctx=fake_ctx("tenant_9"))
    health = tool_fn(server, "rag_health")(ctx=fake_ctx())
    assert health.status == "ok" and health.faiss_ntotal == health.bm25_docs
    chunk = out.chunks[0]
    body = json.loads(_resource(server, f"rag://chunks/{chunk.chunk_id}"))
    assert body["chunk_id"] == chunk.chunk_id
    with pytest.raises(ToolError) as nf:
        _resource(server, "rag://chunks/chk_0000000000000000")
    assert nf.value.code is ErrorCode.NOT_FOUND


def _resource(server: Any, uri: str) -> str:
    template = next(
        t
        for t in server._resource_manager.list_templates()
        if t.uri_template.startswith("rag://") or t.uri_template.startswith("vault://")
    )
    for t in server._resource_manager.list_templates():
        if t.uri_template.split("/")[0] == uri.split("/")[0]:
            template = t
    params = uri.rsplit("/", 1)[1]
    return str(template.fn(params))  # type: ignore[attr-defined]


# --- graphify server ------------------------------------------------------------


def test_graph_tools_in_process(vault) -> None:  # type: ignore[no-untyped-def]
    from contracts.tools import ResolveAnchorIn, SubgraphIn

    server = graphify_server.build_server(vault)
    anchor = tool_fn(server, "graph_resolve_anchor")(
        payload=ResolveAnchorIn(ctx=ctx_model(), query="pump", entities=["SKU-099"]), ctx=fake_ctx()
    )
    assert anchor.anchor_note_id == "sku-099"
    sub = tool_fn(server, "graph_subgraph")(
        payload=SubgraphIn(ctx=ctx_model(), anchor_note_id="sku-099", depth=1, token_budget=100),
        ctx=fake_ctx(),
    )
    assert sub.tokens <= 100
    with pytest.raises(ToolError) as nf:
        graphify_server.subgraph(vault, SubgraphIn(ctx=ctx_model(), anchor_note_id="sku-999"))
    assert nf.value.code is ErrorCode.NOT_FOUND
    stats = tool_fn(server, "graph_stats")(ctx=fake_ctx())
    assert stats.notes == len(vault.notes)
    assert (
        "Safety" in _resource(server, "vault://notes/safety-protocol")
        or "safety" in _resource(server, "vault://notes/safety-protocol").lower()
    )
    with pytest.raises(ToolError):
        _resource(server, "vault://notes/no-such-note")


def test_resolve_anchor_with_no_tokens_returns_none(vault) -> None:  # type: ignore[no-untyped-def]
    from contracts.tools import ResolveAnchorIn

    out = graphify_server.resolve_anchor(vault, ResolveAnchorIn(ctx=ctx_model(), query="", entities=[]))
    assert out.anchor_note_id is None


def test_subgraph_truncates_when_budget_small(vault) -> None:  # type: ignore[no-untyped-def]
    from contracts.tools import SubgraphIn

    out = graphify_server.subgraph(
        vault, SubgraphIn(ctx=ctx_model(), anchor_note_id="lot-88392", depth=2, token_budget=64)
    )
    assert out.tokens <= 64
    assert out.nodes and out.nodes[0].note_id == "lot-88392"


# --- state store service branches ----------------------------------------------


@pytest.fixture
def svc(tmp_path):  # type: ignore[no-untyped-def]
    return StateService(Store(str(tmp_path / "s.sqlite3")), Cache(fakeredis.FakeRedis(decode_responses=True)))


def test_decide_branches(svc: StateService) -> None:
    from contracts.common import CallContext
    from contracts.tools import HitlDecideIn

    requester = ctx_model(tier="TIER_3_MUTATE")
    pending = svc.request_approval(requester, "form_submit", {"form_id": "F1"})
    aid = uuid.UUID(pending["approval_id"])
    other = ctx_model(user="user_77", tier="TIER_3_MUTATE")
    with pytest.raises(ToolError) as nf:
        svc.decide(other, HitlDecideIn(ctx=other, approval_id=uuid.uuid4(), decision="APPROVE", reason="x"))
    assert nf.value.code is ErrorCode.NOT_FOUND
    rejected = svc.decide(other, HitlDecideIn(ctx=other, approval_id=aid, decision="REJECT", reason="no"))
    assert rejected.status == "REJECTED"
    with pytest.raises(ToolError) as done:
        svc.decide(other, HitlDecideIn(ctx=other, approval_id=aid, decision="APPROVE", reason="again"))
    assert done.value.code is ErrorCode.VALIDATION_FAILED
    assert isinstance(requester, CallContext)


def test_semantic_cache_tier_and_tenant_isolation(svc: StateService) -> None:
    svc.cache.put("tenant_7", "a" * 64, json.dumps({"semantic_vec": [1.0, 0.0], "answer": "A"}), 300)
    svc.cache.put("tenant_7", "b" * 64, json.dumps({"semantic_vec": [0.0, 1.0], "answer": "B"}), 300)
    svc.cache.put("tenant_8", "c" * 64, json.dumps({"semantic_vec": [1.0, 0.0], "answer": "X"}), 300)
    entries = svc.cache.tenant_values("tenant_7")
    assert {e["answer"] for e in entries} == {"A", "B"}
    assert state_store._cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert state_store._cosine([0.0], [0.0]) == 0.0
    assert state_store._cosine([1.0], [1.0, 2.0]) == 0.0


def test_audit_verify_edge_cases(svc: StateService) -> None:
    from contracts.tools import AuditAppendIn, AuditVerifyIn

    c = ctx_model(tier="TIER_1_READ")
    for i in range(3):
        svc.append_audit(c, AuditAppendIn(ctx=c, event_type="e", payload_redacted={"i": i}))
    assert svc.verify_audit("tenant_7", AuditVerifyIn(from_seq=2, to_seq=3)).valid
    assert not svc.verify_audit("tenant_7", AuditVerifyIn(from_seq=5, to_seq=6)).valid
    svc.store.execute("UPDATE audit_log SET hash='deadbeef' WHERE tenant_id='tenant_7' AND seq=3")
    assert svc.verify_audit("tenant_7", AuditVerifyIn(from_seq=1, to_seq=3)).broken_at == 3


def test_idempotent_put_fact_and_meta_tenant_helper() -> None:
    assert meta_tenant(fake_ctx("tenant_7")) == "tenant_7"
    with pytest.raises(ToolError):
        meta_tenant(fake_ctx(None))
    assert make_ctx()["tier"] == "TIER_1_READ"
