"""mcp-state-store: streamable-HTTP MCP server (design §1.2, port 8081).

Local backends (design §1.3):
- STATE_BACKEND=sqlite (WAL, default). postgres is NOT implemented in this PoC.
- CACHE_BACKEND=fake (fakeredis, default) | redis (REDIS_URL).

Every table and cache key is tenant-scoped. Audit is a per-tenant SHA-256 chain.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, cast

from mcp.server.fastmcp import Context, FastMCP

from contracts.common import CallContext
from contracts.errors import ErrorCode, ToolError
from contracts.tools import (
    AuditAppendIn,
    AuditAppendOut,
    AuditVerifyIn,
    AuditVerifyOut,
    CacheGetIn,
    CacheGetOut,
    CachePutIn,
    CachePutOut,
    Fact,
    FormSubmitIn,
    HitlDecideIn,
    HitlDecideOut,
    PendingOut,
    SessionGetFactsIn,
    SessionGetFactsOut,
    SessionPutFactIn,
    SessionPutFactOut,
    WorkflowTransitionIn,
)
from mcp_servers.common import check_meta, require_uuid4

GENESIS = "0" * 64
SEMANTIC_THRESHOLD = 0.92
SCHEMA = """
CREATE TABLE IF NOT EXISTS session_facts (
    tenant_id TEXT NOT NULL, session_id TEXT NOT NULL, fact_id TEXT NOT NULL,
    subject TEXT NOT NULL, predicate TEXT NOT NULL, object TEXT NOT NULL,
    source_chunk_ids TEXT NOT NULL, confidence REAL NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY (tenant_id, fact_id));
CREATE INDEX IF NOT EXISTS ix_facts_tenant_session ON session_facts (tenant_id, session_id);
CREATE TABLE IF NOT EXISTS audit_log (
    tenant_id TEXT NOT NULL, seq INTEGER NOT NULL, ts REAL NOT NULL, event_type TEXT NOT NULL,
    payload_redacted TEXT NOT NULL, prev_hash TEXT NOT NULL, hash TEXT NOT NULL,
    PRIMARY KEY (tenant_id, seq));
CREATE TABLE IF NOT EXISTS approvals (
    tenant_id TEXT NOT NULL, approval_id TEXT NOT NULL, status TEXT NOT NULL,
    action TEXT NOT NULL, payload TEXT NOT NULL, requested_by TEXT NOT NULL,
    approver TEXT, reason TEXT, created_at REAL NOT NULL, decided_at REAL, executed_at REAL,
    PRIMARY KEY (tenant_id, approval_id));
CREATE TABLE IF NOT EXISTS idempotency (
    tenant_id TEXT NOT NULL, key TEXT NOT NULL, args_hash TEXT NOT NULL, response TEXT NOT NULL,
    PRIMARY KEY (tenant_id, key));
CREATE TABLE IF NOT EXISTS executed_actions (
    tenant_id TEXT NOT NULL, approval_id TEXT NOT NULL, action TEXT NOT NULL,
    payload TEXT NOT NULL, executed_at REAL NOT NULL);
"""


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def chain_hash(prev_hash: str, event: dict[str, Any]) -> str:
    return hashlib.sha256((prev_hash + canonical_json(event)).encode()).hexdigest()


class Store:
    """Single-connection SQLite store guarded by a lock (PoC; WAL for durability)."""

    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def begin(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")

    def commit(self) -> None:
        self._db.execute("COMMIT")

    def rollback(self) -> None:
        self._db.execute("ROLLBACK")


class Cache:
    """Tenant-prefixed key/value cache with an optional semantic tier (cosine ≥ 0.92)."""

    def __init__(self, client: Any) -> None:
        self._r = client

    @staticmethod
    def _k(tenant_id: str, key: str) -> str:
        return f"t:{tenant_id}:{key}"

    def get(self, tenant_id: str, key: str) -> str | None:
        raw = self._r.get(self._k(tenant_id, key))
        return None if raw is None else str(raw)

    def put(self, tenant_id: str, key: str, value: str, ttl_s: int) -> None:
        self._r.set(self._k(tenant_id, key), value, ex=ttl_s)

    def tenant_values(self, tenant_id: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for k in self._r.scan_iter(match=f"t:{tenant_id}:*"):
            raw = self._r.get(k)
            if raw is not None:
                out.append(json.loads(raw))
        return out


def _cosine(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0 or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / (na * nb)


class StateService:
    def __init__(self, store: Store, cache: Cache) -> None:
        self.store = store
        self.cache = cache

    # --- idempotency (design §1.2 table: idempotency(tenant_id, key, response_hash)) ---
    def idempotent(self, ctx: CallContext, args: dict[str, Any], run: Any) -> dict[str, Any]:
        key = str(require_uuid4(ctx))
        args_hash = hashlib.sha256(canonical_json(args).encode()).hexdigest()
        rows = self.store.execute(
            "SELECT args_hash, response FROM idempotency WHERE tenant_id=? AND key=?",
            (ctx.tenant_id, key),
        )
        if rows:
            if rows[0]["args_hash"] != args_hash:
                raise ToolError(ErrorCode.IDEMPOTENCY_CONFLICT, "same key, different arguments")
            return cast(dict[str, Any], json.loads(rows[0]["response"]))
        result = run()
        self.store.execute(
            "INSERT INTO idempotency (tenant_id, key, args_hash, response) VALUES (?,?,?,?)",
            (ctx.tenant_id, key, args_hash, canonical_json(result)),
        )
        return cast(dict[str, Any], result)

    # --- session facts -----------------------------------------------------------
    def get_facts(self, ctx: CallContext, limit: int) -> SessionGetFactsOut:
        rows = self.store.execute(
            "SELECT subject, predicate, object, source_chunk_ids, confidence FROM session_facts "
            "WHERE tenant_id=? AND session_id=? ORDER BY created_at DESC LIMIT ?",
            (ctx.tenant_id, ctx.session_id, limit),
        )
        return SessionGetFactsOut(
            facts=[
                Fact(
                    subject=r["subject"],
                    predicate=r["predicate"],
                    object=r["object"],
                    source_chunk_ids=json.loads(r["source_chunk_ids"]),
                    confidence=r["confidence"],
                )
                for r in rows
            ]
        )

    def put_fact(self, ctx: CallContext, fact: Fact) -> dict[str, Any]:
        fact_id = str(uuid.uuid4())
        self.store.execute(
            "INSERT INTO session_facts (tenant_id, session_id, fact_id, subject, predicate, object,"
            " source_chunk_ids, confidence, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                ctx.tenant_id,
                ctx.session_id,
                fact_id,
                fact.subject,
                fact.predicate,
                fact.object,
                json.dumps(fact.source_chunk_ids),
                fact.confidence,
                time.time(),
            ),
        )
        return {"fact_id": fact_id}

    # --- HITL (Tier 3) ----------------------------------------------------------
    def request_approval(self, ctx: CallContext, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        approval_id = str(uuid.uuid4())
        self.store.execute(
            "INSERT INTO approvals (tenant_id, approval_id, status, action, payload, requested_by,"
            " created_at) VALUES (?,?,?,?,?,?,?)",
            (
                ctx.tenant_id,
                approval_id,
                "PENDING_APPROVAL",
                action,
                canonical_json(payload),
                ctx.user_id,
                time.time(),
            ),
        )
        return {"status": "PENDING_APPROVAL", "approval_id": approval_id}

    def decide(self, ctx: CallContext, req: HitlDecideIn) -> HitlDecideOut:
        rows = self.store.execute(
            "SELECT status, action, payload, requested_by FROM approvals WHERE tenant_id=? AND approval_id=?",
            (ctx.tenant_id, str(req.approval_id)),
        )
        if not rows:
            raise ToolError(ErrorCode.NOT_FOUND, "approval not found")
        row = rows[0]
        if row["status"] != "PENDING_APPROVAL":
            raise ToolError(ErrorCode.VALIDATION_FAILED, f"already {row['status']}")
        if ctx.user_id == row["requested_by"]:
            raise ToolError(ErrorCode.RBAC_DENIED, "approver must differ from requester")
        now = time.time()
        if req.decision == "REJECT":
            self.store.execute(
                "UPDATE approvals SET status='REJECTED', approver=?, reason=?, decided_at=?"
                " WHERE tenant_id=? AND approval_id=?",
                (ctx.user_id, req.reason, now, ctx.tenant_id, str(req.approval_id)),
            )
            return HitlDecideOut(status="REJECTED")
        self.store.begin()
        try:
            self.store.execute(
                "UPDATE approvals SET status='APPROVED', approver=?, reason=?, decided_at=?,"
                " executed_at=? WHERE tenant_id=? AND approval_id=?",
                (ctx.user_id, req.reason, now, now, ctx.tenant_id, str(req.approval_id)),
            )
            self.store.execute(
                "INSERT INTO executed_actions"
                " (tenant_id, approval_id, action, payload, executed_at) VALUES (?,?,?,?,?)",
                (ctx.tenant_id, str(req.approval_id), row["action"], row["payload"], now),
            )
            self.store.commit()
        except Exception:
            self.store.rollback()
            raise
        return HitlDecideOut(status="APPROVED", executed_at=now)

    # --- audit (per-tenant SHA-256 chain) ----------------------------------------
    def append_audit(self, ctx: CallContext, req: AuditAppendIn) -> AuditAppendOut:
        self.store.begin()
        try:
            last = self.store.execute(
                "SELECT seq, hash FROM audit_log WHERE tenant_id=? ORDER BY seq DESC LIMIT 1",
                (ctx.tenant_id,),
            )
            seq = (last[0]["seq"] + 1) if last else 1
            prev = last[0]["hash"] if last else GENESIS
            event = {
                "seq": seq,
                "ts": time.time(),
                "tenant_id": ctx.tenant_id,
                "event_type": req.event_type,
                "payload_redacted": req.payload_redacted,
            }
            digest = chain_hash(prev, event)
            self.store.execute(
                "INSERT INTO audit_log (tenant_id, seq, ts, event_type, payload_redacted,"
                " prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
                (
                    ctx.tenant_id,
                    seq,
                    event["ts"],
                    req.event_type,
                    canonical_json(req.payload_redacted),
                    prev,
                    digest,
                ),
            )
            self.store.commit()
        except Exception:
            self.store.rollback()
            raise
        return AuditAppendOut(seq=seq, hash=digest)

    def verify_audit(self, tenant_id: str, req: AuditVerifyIn) -> AuditVerifyOut:
        rows = self.store.execute(
            "SELECT seq, ts, event_type, payload_redacted, prev_hash, hash FROM audit_log"
            " WHERE tenant_id=? AND seq BETWEEN ? AND ? ORDER BY seq",
            (tenant_id, req.from_seq, req.to_seq),
        )
        expected_prev = GENESIS
        if req.from_seq > 1:
            prior = self.store.execute(
                "SELECT hash FROM audit_log WHERE tenant_id=? AND seq=?",
                (tenant_id, req.from_seq - 1),
            )
            if not prior:
                return AuditVerifyOut(valid=False, broken_at=req.from_seq)
            expected_prev = prior[0]["hash"]
        for r in rows:
            if r["prev_hash"] != expected_prev:
                return AuditVerifyOut(valid=False, broken_at=r["seq"])
            event = {
                "seq": r["seq"],
                "ts": r["ts"],
                "tenant_id": tenant_id,
                "event_type": r["event_type"],
                "payload_redacted": json.loads(r["payload_redacted"]),
            }
            if chain_hash(r["prev_hash"], event) != r["hash"]:
                return AuditVerifyOut(valid=False, broken_at=r["seq"])
            expected_prev = r["hash"]
        return AuditVerifyOut(valid=True)


def build_server(service: StateService, *, stateless: bool = True) -> FastMCP:
    mcp = FastMCP(
        "state-store",
        stateless_http=stateless,
        json_response=True,
        host="127.0.0.1",
        port=8081,
    )
    svc = service

    @mcp.tool(name="cache_get")
    def cache_get(payload: CacheGetIn, ctx: Context[Any, Any, Any]) -> CacheGetOut:
        check_meta(ctx, payload.ctx.tenant_id)
        tenant = payload.ctx.tenant_id
        raw = svc.cache.get(tenant, payload.key)
        if raw is not None:
            return CacheGetOut(hit=True, tier="exact", value=json.loads(raw))
        if payload.semantic_vec is None:
            return CacheGetOut(hit=False)
        best: tuple[float, dict[str, Any]] | None = None
        for entry in svc.cache.tenant_values(tenant):
            vec = entry.get("semantic_vec")
            if not isinstance(vec, list):
                continue
            cos = _cosine(payload.semantic_vec, [float(x) for x in vec])
            if cos >= SEMANTIC_THRESHOLD and (best is None or cos > best[0]):
                best = (cos, entry)
        if best is None:
            return CacheGetOut(hit=False)
        return CacheGetOut(hit=True, tier="semantic", value=best[1], cosine=round(best[0], 6))

    @mcp.tool(name="cache_put")
    def cache_put(payload: CachePutIn, ctx: Context[Any, Any, Any]) -> CachePutOut:
        check_meta(ctx, payload.ctx.tenant_id)
        svc.cache.put(payload.ctx.tenant_id, payload.key, canonical_json(payload.value), payload.ttl_s)
        return CachePutOut(stored=True)

    @mcp.tool(name="session_get_facts")
    def session_get_facts(payload: SessionGetFactsIn, ctx: Context[Any, Any, Any]) -> SessionGetFactsOut:
        check_meta(ctx, payload.ctx.tenant_id)
        return svc.get_facts(payload.ctx, payload.limit)

    @mcp.tool(name="session_put_fact")
    def session_put_fact(payload: SessionPutFactIn, ctx: Context[Any, Any, Any]) -> SessionPutFactOut:
        check_meta(ctx, payload.ctx.tenant_id)
        args = payload.model_dump(mode="json")
        result = svc.idempotent(payload.ctx, args, lambda: svc.put_fact(payload.ctx, payload.fact))
        return SessionPutFactOut(fact_id=uuid.UUID(result["fact_id"]))

    @mcp.tool(name="workflow_transition")
    def workflow_transition(payload: WorkflowTransitionIn, ctx: Context[Any, Any, Any]) -> PendingOut:
        check_meta(ctx, payload.ctx.tenant_id)
        args = payload.model_dump(mode="json")
        body = {
            "entity": payload.entity,
            "from_state": payload.from_state,
            "to_state": payload.to_state,
            "payload": payload.payload,
        }
        result = svc.idempotent(
            payload.ctx,
            args,
            lambda: svc.request_approval(payload.ctx, "workflow_transition", body),
        )
        return PendingOut(status="PENDING_APPROVAL", approval_id=uuid.UUID(result["approval_id"]))

    @mcp.tool(name="form_submit")
    def form_submit(payload: FormSubmitIn, ctx: Context[Any, Any, Any]) -> PendingOut:
        check_meta(ctx, payload.ctx.tenant_id)
        args = payload.model_dump(mode="json")
        body = {"form_id": payload.form_id, "fields": payload.fields}
        result = svc.idempotent(payload.ctx, args, lambda: svc.request_approval(payload.ctx, "form_submit", body))
        return PendingOut(status="PENDING_APPROVAL", approval_id=uuid.UUID(result["approval_id"]))

    @mcp.tool(name="hitl_decide")
    def hitl_decide(payload: HitlDecideIn, ctx: Context[Any, Any, Any]) -> HitlDecideOut:
        check_meta(ctx, payload.ctx.tenant_id)
        return svc.decide(payload.ctx, payload)

    @mcp.tool(name="audit_append")
    def audit_append(payload: AuditAppendIn, ctx: Context[Any, Any, Any]) -> AuditAppendOut:
        check_meta(ctx, payload.ctx.tenant_id)
        return svc.append_audit(payload.ctx, payload)

    @mcp.tool(name="audit_verify")
    def audit_verify(payload: AuditVerifyIn, ctx: Context[Any, Any, Any]) -> AuditVerifyOut:
        return svc.verify_audit(meta_tenant(ctx), payload)

    return mcp


def meta_tenant(ctx: Context[Any, Any, Any]) -> str:
    """audit_verify has no CallContext (design §1.2: from_seq/to_seq only); tenant is _meta."""
    try:
        meta = ctx.request_context.meta
    except ValueError:
        meta = None
    tenant = getattr(meta, "tenant_id", None)
    if not isinstance(tenant, str):
        raise ToolError(ErrorCode.INVALID_REQUEST, "_meta.tenant_id missing")
    return tenant


def make_service(db_path: str, cache_backend: str) -> StateService:
    if cache_backend == "redis":
        import redis

        client: Any = redis.Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    else:
        import fakeredis

        client = fakeredis.FakeRedis(decode_responses=True)
    return StateService(Store(db_path), Cache(client))


def main() -> None:
    parser = argparse.ArgumentParser(description="mcp-state-store (streamable-HTTP)")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument("--db", default=os.environ.get("STATE_DB", "state.sqlite3"))
    args = parser.parse_args()
    backend = os.environ.get("STATE_BACKEND", "sqlite")
    if backend != "sqlite":
        raise SystemExit(f"STATE_BACKEND={backend} not implemented in this PoC")
    service = make_service(str(Path(args.db)), os.environ.get("CACHE_BACKEND", "fake"))
    server = build_server(service)
    server.settings.port = args.port
    server.run(transport="streamable-http")


if __name__ == "__main__":
    main()
