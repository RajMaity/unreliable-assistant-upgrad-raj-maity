import pytest
from pydantic import ValidationError

from contracts.tools import ResolveAnchorIn, SubgraphIn
from mcp_servers.graphify_server import resolve_anchor, subgraph
from tests.conftest import ctx_model

CTX = ctx_model()


def test_sku_entity_resolves_to_its_note(vault) -> None:  # type: ignore[no-untyped-def]
    out = resolve_anchor(vault, ResolveAnchorIn(ctx=CTX, query="pump status", entities=["SKU-099"]))
    assert out.anchor_note_id == "sku-099"
    assert out.score == 1.0


def test_no_match_returns_none(vault) -> None:  # type: ignore[no-untyped-def]
    out = resolve_anchor(vault, ResolveAnchorIn(ctx=CTX, query="zzzz qqqq"))
    assert out.anchor_note_id is None
    assert out.score == 0.0


def test_subgraph_is_bounded_and_reports_compression(vault) -> None:  # type: ignore[no-untyped-def]
    out = subgraph(vault, SubgraphIn(ctx=CTX, anchor_note_id="lot-88392", depth=2, token_budget=120))
    assert out.nodes[0].note_id == "lot-88392" and out.nodes[0].hop == 0
    assert out.tokens <= 120
    assert out.raw_tokens_est >= out.tokens
    assert all(n.hop <= 2 for n in out.nodes)


def test_edges_only_between_included_nodes(vault) -> None:  # type: ignore[no-untyped-def]
    out = subgraph(vault, SubgraphIn(ctx=CTX, anchor_note_id="sku-099", depth=1, token_budget=600))
    ids = {n.note_id for n in out.nodes}
    assert all(a in ids and b in ids for a, b in out.edges)


def test_depth_hard_capped_at_two() -> None:
    with pytest.raises(ValidationError):
        SubgraphIn(ctx=CTX, anchor_note_id="sku-099", depth=3)
