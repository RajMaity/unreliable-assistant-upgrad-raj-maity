import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.conftest import make_ctx, meta

ROOT = Path(__file__).resolve().parents[2]


def _params(module: str) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", module], cwd=str(ROOT))


def _payload(result) -> dict:  # type: ignore[no-untyped-def]
    assert not result.isError, result.content
    return json.loads(result.content[0].text)


@pytest.mark.asyncio
async def test_rag_stdio_hybrid_retrieve_and_health() -> None:
    async with stdio_client(_params("mcp_servers.rag_server")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = {t.name for t in (await s.list_tools()).tools}
            assert {"hybrid_retrieve", "rag_health"} <= tools
            res = await s.call_tool(
                "hybrid_retrieve",
                {"payload": {"ctx": make_ctx(), "query": "LOT-88392 safety protocol"}},
                meta=meta(),
            )
            out = _payload(res)
            assert out["chunks"][0]["note_id"] == "lot-88392"
            assert set(out["stage_ms"]) == {"bm25", "dense", "rrf", "rerank"}
            health = _payload(await s.call_tool("rag_health", {}, meta=meta()))
            assert health["status"] == "ok" and health["bm25_docs"] > 0


@pytest.mark.asyncio
async def test_rag_rejects_tenant_mismatch_in_meta() -> None:
    async with stdio_client(_params("mcp_servers.rag_server")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(
                "hybrid_retrieve",
                {"payload": {"ctx": make_ctx(), "query": "SKU-099"}},
                meta=meta("tenant_other"),
            )
            assert res.isError
            assert "-32001" in res.content[0].text


@pytest.mark.asyncio
async def test_rag_rejects_invalid_args_with_validation_error() -> None:
    async with stdio_client(_params("mcp_servers.rag_server")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool(
                "hybrid_retrieve",
                {"payload": {"ctx": make_ctx(), "query": "x", "top_k_sparse": 1}},
                meta=meta(),
            )
            assert res.isError


@pytest.mark.asyncio
async def test_graphify_stdio_resolve_then_subgraph() -> None:
    async with stdio_client(_params("mcp_servers.graphify_server")) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            anchor = _payload(
                await s.call_tool(
                    "graph_resolve_anchor",
                    {"payload": {"ctx": make_ctx(), "query": "pump", "entities": ["SKU-099"]}},
                    meta=meta(),
                )
            )
            assert anchor["anchor_note_id"] == "sku-099"
            sub = _payload(
                await s.call_tool(
                    "graph_subgraph",
                    {
                        "payload": {
                            "ctx": make_ctx(),
                            "anchor_note_id": "sku-099",
                            "depth": 2,
                            "token_budget": 200,
                        }
                    },
                    meta=meta(),
                )
            )
            assert sub["tokens"] <= 200 and sub["nodes"][0]["note_id"] == "sku-099"
