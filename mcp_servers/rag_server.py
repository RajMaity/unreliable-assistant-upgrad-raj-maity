"""mcp-hybrid-rag: stdio MCP server exposing hybrid_retrieve, rag_health and rag://chunks/{id}."""

from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from contracts.errors import ErrorCode, ToolError
from contracts.tools import ChunkResource, HybridRetrieveIn, HybridRetrieveOut, RagHealthOut
from mcp_servers.common import check_meta
from mcp_servers.retrieval import Index, hybrid_retrieve
from mcp_servers.vault import Vault, load_vault


def build_server(vault: Vault | None = None) -> FastMCP:
    vault = vault or load_vault()
    index = Index.build(vault)
    chunks_by_id = {c.chunk_id: c for c in vault.chunks}
    mcp = FastMCP("hybrid-rag")

    @mcp.tool(name="hybrid_retrieve")
    def hybrid_retrieve_tool(payload: HybridRetrieveIn, ctx: Context[Any, Any, Any]) -> HybridRetrieveOut:
        check_meta(ctx, payload.ctx.tenant_id)
        return hybrid_retrieve(index, payload)

    @mcp.tool(name="rag_health")
    def rag_health(ctx: Context[Any, Any, Any]) -> RagHealthOut:
        return RagHealthOut(
            status="ok" if index.bm25_docs > 0 else "degraded",
            index_version=index.version,
            bm25_docs=index.bm25_docs,
            faiss_ntotal=int(index.dense.shape[0]),
        )

    @mcp.resource("rag://chunks/{chunk_id}")
    def chunk_resource(chunk_id: str) -> str:
        chunk = chunks_by_id.get(chunk_id)
        if chunk is None:
            raise ToolError(ErrorCode.NOT_FOUND, f"chunk {chunk_id} not found")
        res = ChunkResource(
            chunk_id=chunk.chunk_id,
            chunk_hash=chunk.chunk_hash,
            note_id=chunk.note_id,
            text=chunk.text,
        )
        return res.model_dump_json()

    return mcp


if __name__ == "__main__":
    build_server().run(transport="stdio")
