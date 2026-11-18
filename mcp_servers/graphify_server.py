"""mcp-graphify-vault: stdio MCP server for anchor resolution and bounded subgraphs.

Depth is hard-capped at 2 server-side (design §1.2). Token budget bounds the
returned text; `raw_tokens_est` reports the uncompressed size for KPI-26.
"""

import re
from typing import Any, cast

from mcp.server.fastmcp import Context, FastMCP

from contracts.common import Tier
from contracts.errors import ErrorCode, ToolError
from contracts.tools import (
    GraphStatsOut,
    ResolveAnchorIn,
    ResolveAnchorOut,
    SubgraphIn,
    SubgraphNode,
    SubgraphOut,
)
from mcp_servers.common import check_meta
from mcp_servers.retrieval import tokenize
from mcp_servers.vault import Note, Vault, estimate_tokens, load_vault

MAX_DEPTH = 2
HEADING_REF = re.compile(r"\b(?:SKU-\d{3}|LOT-\d{5})\b")


def _refs(note: Note) -> set[str]:
    """Entity refs a note is *about*: its id plus SKU/LOT codes in its title line."""
    title = next((ln for ln in note.body.splitlines() if ln.startswith("# ")), "")
    return {note.note_id.upper(), *HEADING_REF.findall(title)}


def resolve_anchor(vault: Vault, req: ResolveAnchorIn) -> ResolveAnchorOut:
    entities = {e.upper() for e in req.entities}
    q_tokens = set(tokenize(" ".join([req.query, *req.entities])))
    best_id: str | None = None
    best_key: tuple[bool, float] = (False, 0.0)
    best = 0.0
    for nid in sorted(vault.notes):
        note = vault.notes[nid]
        # An explicit entity that names the note outranks any text-overlap score.
        entity_hit = bool(entities & _refs(note))
        text = len(q_tokens & set(tokenize(note.body))) / len(q_tokens) if q_tokens else 0.0
        key = (entity_hit, 1.0 if entity_hit else text)
        if key > best_key:
            best_id, best_key = nid, key
            best = key[1]
    if best_key[1] == 0.0:
        best_id = None
    return ResolveAnchorOut(anchor_note_id=best_id, score=round(best, 6))


def _neighbours(vault: Vault, nid: str) -> set[str]:
    out = vault.out_edges.get(nid, set())
    inc = {src for src, dsts in vault.out_edges.items() if nid in dsts}
    return (out | inc) - {nid}


def subgraph(vault: Vault, req: SubgraphIn) -> SubgraphOut:
    if req.anchor_note_id not in vault.notes:
        raise ToolError(ErrorCode.NOT_FOUND, f"unknown anchor {req.anchor_note_id}")
    depth = min(req.depth, MAX_DEPTH)
    hop_of: dict[str, int] = {req.anchor_note_id: 0}
    frontier = [req.anchor_note_id]
    for hop in range(1, depth + 1):
        nxt: list[str] = []
        for nid in frontier:
            for nb in sorted(_neighbours(vault, nid)):
                if nb not in hop_of:
                    hop_of[nb] = hop
                    nxt.append(nb)
        frontier = nxt

    raw_tokens = sum(estimate_tokens(vault.notes[n].body) for n in hop_of)
    ordered = sorted(hop_of, key=lambda n: (hop_of[n], n))
    nodes: list[SubgraphNode] = []
    used = 0
    for nid in ordered:
        note = vault.notes[nid]
        remaining = req.token_budget - used
        if remaining <= 0:
            break
        text = note.body
        if estimate_tokens(text) > remaining:
            words = text.split()
            keep = max(int(remaining / 1.3), 0)
            text = " ".join(words[:keep])
            if not text:
                break
        cost = estimate_tokens(text)
        used += cost
        nodes.append(
            SubgraphNode(
                note_id=nid,
                domain=note.domain,
                risk_tier=cast(Tier, note.risk_tier),
                version=note.version,
                text=text,
                hop=hop_of[nid],
            )
        )
    included = {n.note_id for n in nodes}
    edges = sorted((src, dst) for src in included for dst in vault.out_edges.get(src, set()) if dst in included)
    return SubgraphOut(nodes=nodes, edges=edges, tokens=used, raw_tokens_est=raw_tokens)


def build_server(vault: Vault | None = None) -> FastMCP:
    vault = vault or load_vault()
    mcp = FastMCP("graphify-vault")

    @mcp.tool(name="graph_resolve_anchor")
    def graph_resolve_anchor(payload: ResolveAnchorIn, ctx: Context[Any, Any, Any]) -> ResolveAnchorOut:
        check_meta(ctx, payload.ctx.tenant_id)
        return resolve_anchor(vault, payload)

    @mcp.tool(name="graph_subgraph")
    def graph_subgraph(payload: SubgraphIn, ctx: Context[Any, Any, Any]) -> SubgraphOut:
        check_meta(ctx, payload.ctx.tenant_id)
        return subgraph(vault, payload)

    @mcp.tool(name="graph_stats")
    def graph_stats(ctx: Context[Any, Any, Any]) -> GraphStatsOut:
        return GraphStatsOut(
            notes=len(vault.notes),
            edges=sum(len(v) for v in vault.out_edges.values()),
            index_version=vault.version,
        )

    @mcp.resource("vault://notes/{note_id}")
    def note_resource(note_id: str) -> str:
        note = vault.notes.get(note_id)
        if note is None:
            raise ToolError(ErrorCode.NOT_FOUND, f"note {note_id} not found")
        return note.body

    return mcp


if __name__ == "__main__":
    build_server().run(transport="stdio")
