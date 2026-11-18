from contracts.tools import HybridRetrieveIn
from mcp_servers.retrieval import Index, hybrid_retrieve
from tests.conftest import ctx_model


def _req(query: str, **kw: object) -> HybridRetrieveIn:
    return HybridRetrieveIn(ctx=ctx_model(), query=query, **kw)  # type: ignore[arg-type]


def test_lot_query_returns_lot_note_first(vault) -> None:  # type: ignore[no-untyped-def]
    out = hybrid_retrieve(Index.build(vault), _req("LOT-88392 safety protocol"))
    assert not out.abstain
    assert out.chunks, "expected at least one chunk"
    assert out.chunks[0].note_id == "lot-88392"
    assert all(c.ce_score is not None and c.ce_score > 0.42 for c in out.chunks)


def test_unrelated_query_abstains(vault) -> None:  # type: ignore[no-untyped-def]
    out = hybrid_retrieve(Index.build(vault), _req("quantum chromodynamics lecture"))
    assert out.abstain
    assert out.chunks == []


def test_rerank_k_caps_result_count(vault) -> None:  # type: ignore[no-untyped-def]
    out = hybrid_retrieve(Index.build(vault), _req("safety storage returns shipment", rerank_k=2, threshold=0.0))
    assert len(out.chunks) <= 2


def test_stage_timings_reported_for_all_stages(vault) -> None:  # type: ignore[no-untyped-def]
    out = hybrid_retrieve(Index.build(vault), _req("SKU-099 pump"))
    assert set(out.stage_ms) == {"bm25", "dense", "rrf", "rerank"}
    assert all(v >= 0 for v in out.stage_ms.values())


def test_index_is_versioned_by_content(vault) -> None:  # type: ignore[no-untyped-def]
    assert len(Index.build(vault).version) == 12
