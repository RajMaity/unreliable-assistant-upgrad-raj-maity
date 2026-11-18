"""MCP tool input/output models. Source: design.md §1.2.

Models marked PROPOSED are not defined in design.md (it lists only a table or
a name for them). They follow the table's field names and are NOT locked until
an ADR amending design.md is approved (CLAUDE.md §3).

Boundary note: Strict uses strict=True, so JSON-borne UUID values must be parsed
with model_validate_json (not model_validate on a dict of str) at the JSON-RPC edge.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, JsonValue, StringConstraints

from contracts.common import LOT, SKU, CallContext, ChunkId, NoteId, Strict, Tier

Hex64 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
NonEmpty2000 = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
Stage = Literal["bm25", "dense", "rrf", "rerank"]
Decision = Literal["APPROVE", "REJECT"]
Status = Literal["PENDING_APPROVAL"]


# --- mcp-hybrid-rag ---------------------------------------------------------


class HybridRetrieveIn(Strict):
    ctx: CallContext
    query: NonEmpty2000
    top_k_sparse: int = Field(50, ge=5, le=100)
    top_k_dense: int = Field(50, ge=5, le=100)
    rrf_k: int = Field(60, ge=1, le=200)
    pool: int = Field(40, ge=4, le=100)
    rerank_k: int = Field(4, ge=1, le=10)
    threshold: float = Field(0.42, ge=0.0, le=1.0)
    k1: float = Field(1.25, gt=0, le=3.0)
    b: float = Field(0.75, ge=0, le=1.0)


class ScoredChunk(Strict):
    chunk_id: ChunkId
    chunk_hash: str
    note_id: NoteId | None
    text: str
    bm25: float | None
    cosine: float | None
    rrf_rank: int | None
    ce_score: float | None  # sigmoid, [0,1]


class HybridRetrieveOut(Strict):
    chunks: list[ScoredChunk]
    abstain: bool
    top_score: float
    stage_ms: dict[Stage, float]


class ChunkResource(Strict):  # PROPOSED: resource rag://chunks/{id} (schema not in design.md)
    chunk_id: ChunkId
    chunk_hash: str
    note_id: NoteId | None
    text: str


class RagHealthOut(Strict):  # fields per design.md §1.2; model name PROPOSED
    status: Literal["ok", "degraded"]
    index_version: str
    bm25_docs: int
    faiss_ntotal: int


# --- mcp-state-store --------------------------------------------------------


class Fact(Strict):
    subject: str
    predicate: str
    object: str
    source_chunk_ids: list[ChunkId]
    confidence: float = Field(ge=0, le=1)


class CacheGetIn(Strict):
    ctx: CallContext
    key: Hex64
    semantic_vec: list[float] | None = None


class CacheGetOut(Strict):
    hit: bool
    tier: Literal["exact", "semantic"] | None = None
    value: dict[str, JsonValue] | None = None
    cosine: float | None = None


class CachePutIn(Strict):
    ctx: CallContext
    key: Hex64
    value: dict[str, JsonValue]
    ttl_s: int = Field(ge=255, le=345)


class CachePutOut(Strict):
    stored: bool


class SessionGetFactsIn(Strict):
    ctx: CallContext
    limit: int = Field(ge=1, le=50)


class SessionGetFactsOut(Strict):
    facts: list[Fact]


class SessionPutFactIn(Strict):
    ctx: CallContext
    fact: Fact  # verified facts only (caller-enforced)


class SessionPutFactOut(Strict):
    fact_id: UUID


class WorkflowTransitionIn(Strict):
    ctx: CallContext
    entity: SKU | LOT
    from_state: str
    to_state: str
    payload: dict[str, JsonValue]


class FormSubmitIn(Strict):  # PROPOSED: form_id/fields types not defined in design.md
    ctx: CallContext
    form_id: str
    fields: dict[str, JsonValue]


class PendingOut(Strict):
    status: Status
    approval_id: UUID


class HitlDecideIn(Strict):
    ctx: CallContext
    approval_id: Annotated[UUID, Field(strict=False)]  # wire form is a JSON string
    decision: Decision
    reason: str


class HitlDecideOut(Strict):  # PROPOSED: status vocabulary not defined
    status: str
    executed_at: float | None = None


class AuditAppendIn(Strict):
    ctx: CallContext
    event_type: str
    payload_redacted: dict[str, JsonValue]


class AuditAppendOut(Strict):
    seq: int
    hash: str


class AuditVerifyIn(Strict):
    from_seq: int = Field(ge=0)
    to_seq: int = Field(ge=0)


class AuditVerifyOut(Strict):
    valid: bool
    broken_at: int | None = None


class AuditEvent(Strict):
    seq: int
    ts: float
    tenant_id: str
    event_type: str
    payload_redacted: dict[str, JsonValue]
    prev_hash: str
    hash: str


# --- mcp-graphify-vault -----------------------------------------------------


class ResolveAnchorIn(Strict):
    ctx: CallContext
    query: str
    entities: list[SKU | LOT | str] = []


class ResolveAnchorOut(Strict):
    anchor_note_id: NoteId | None
    score: float


class SubgraphIn(Strict):
    ctx: CallContext
    anchor_note_id: NoteId
    depth: int = Field(2, ge=0, le=2)
    token_budget: int = Field(320, ge=64, le=600)


class SubgraphNode(Strict):
    note_id: NoteId
    domain: str
    risk_tier: Tier
    version: str
    text: str
    hop: int


class SubgraphOut(Strict):
    nodes: list[SubgraphNode]
    edges: list[tuple[NoteId, NoteId]]
    tokens: int
    raw_tokens_est: int


class GraphStatsOut(Strict):  # PROPOSED: graph_stats schema not defined in design.md
    notes: int
    edges: int
    index_version: str
