import json
import uuid

import pytest
from pydantic import ValidationError

from contracts.common import CallContext
from contracts.tools import (
    CacheGetIn,
    CachePutIn,
    Fact,
    HybridRetrieveIn,
    ResolveAnchorIn,
    SessionGetFactsIn,
    SubgraphIn,
    WorkflowTransitionIn,
)

CTX = {
    "tenant_id": "tenant_7",
    "user_id": "user_42",
    "session_id": "sess_ab12cd34ef",
    "idempotency_key": str(uuid.uuid4()),
    "tier": "TIER_1_READ",
}
KEY64 = "a" * 64


def _parse(model: type, payload: dict) -> object:
    return model.model_validate_json(json.dumps(payload))  # type: ignore[attr-defined]


def test_hybrid_retrieve_defaults_match_design() -> None:
    m = _parse(HybridRetrieveIn, {"ctx": CTX, "query": "LOT-88392 safety"})
    assert isinstance(m, HybridRetrieveIn)
    assert (m.top_k_sparse, m.top_k_dense, m.rrf_k, m.pool, m.rerank_k) == (50, 50, 60, 40, 4)
    assert m.threshold == 0.42
    assert (m.k1, m.b) == (1.25, 0.75)


@pytest.mark.parametrize(
    "overrides",
    [{"query": ""}, {"query": "x" * 2001}, {"top_k_sparse": 4}, {"rerank_k": 11}, {"k1": 0}],
)
def test_hybrid_retrieve_bounds_enforced(overrides: dict[str, object]) -> None:
    payload = {"ctx": CTX, "query": "q", **overrides}
    with pytest.raises(ValidationError):
        _parse(HybridRetrieveIn, payload)


def test_hybrid_retrieve_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        _parse(HybridRetrieveIn, {"ctx": CTX, "query": "q", "debug": True})


def test_resolve_anchor_accepts_sku_and_free_text_entities() -> None:
    m = _parse(ResolveAnchorIn, {"ctx": CTX, "query": "q", "entities": ["SKU-099", "widget"]})
    assert isinstance(m, ResolveAnchorIn)
    assert m.entities == ["SKU-099", "widget"]


def test_subgraph_depth_hard_capped_at_two() -> None:
    base = {"ctx": CTX, "anchor_note_id": "sku-099-overview"}
    assert isinstance(_parse(SubgraphIn, {**base, "depth": 2}), SubgraphIn)
    with pytest.raises(ValidationError):
        _parse(SubgraphIn, {**base, "depth": 3})


def test_subgraph_token_budget_bounds() -> None:
    base = {"ctx": CTX, "anchor_note_id": "sku-099-overview"}
    with pytest.raises(ValidationError):
        _parse(SubgraphIn, {**base, "token_budget": 63})
    with pytest.raises(ValidationError):
        _parse(SubgraphIn, {**base, "token_budget": 601})


def test_cache_get_key_must_be_64_hex() -> None:
    assert isinstance(_parse(CacheGetIn, {"ctx": CTX, "key": KEY64}), CacheGetIn)
    with pytest.raises(ValidationError):
        _parse(CacheGetIn, {"ctx": CTX, "key": "xyz"})


def test_cache_put_ttl_window() -> None:
    base = {"ctx": CTX, "key": KEY64, "value": {"a": 1}}
    assert isinstance(_parse(CachePutIn, {**base, "ttl_s": 300}), CachePutIn)
    with pytest.raises(ValidationError):
        _parse(CachePutIn, {**base, "ttl_s": 254})
    with pytest.raises(ValidationError):
        _parse(CachePutIn, {**base, "ttl_s": 346})


def test_session_get_facts_limit_max_50() -> None:
    with pytest.raises(ValidationError):
        _parse(SessionGetFactsIn, {"ctx": CTX, "limit": 51})


def test_fact_confidence_range_and_chunk_ids() -> None:
    good = {
        "subject": "SKU-099",
        "predicate": "has_status",
        "object": "active",
        "source_chunk_ids": ["chk_0123456789abcdef"],
        "confidence": 0.9,
    }
    assert isinstance(_parse(Fact, good), Fact)
    with pytest.raises(ValidationError):
        _parse(Fact, {**good, "confidence": 1.5})
    with pytest.raises(ValidationError):
        _parse(Fact, {**good, "source_chunk_ids": ["bad"]})


def test_workflow_transition_entity_restricted_to_sku_or_lot() -> None:
    base = {
        "ctx": {**CTX, "tier": "TIER_3_MUTATE", "idempotency_key": str(uuid.uuid4())},
        "from_state": "open",
        "to_state": "closed",
        "payload": {},
    }
    assert isinstance(_parse(WorkflowTransitionIn, {**base, "entity": "LOT-00044"}), WorkflowTransitionIn)
    with pytest.raises(ValidationError):
        _parse(WorkflowTransitionIn, {**base, "entity": "widget"})


def test_call_context_inside_tool_is_frozen_and_typed() -> None:
    m = _parse(SessionGetFactsIn, {"ctx": CTX, "limit": 5})
    assert isinstance(m, SessionGetFactsIn)
    assert isinstance(m.ctx, CallContext)
