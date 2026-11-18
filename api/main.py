"""FastAPI gateway (PROPOSED entry module: api.main:app). Single Uvicorn worker.

Run: uv run uvicorn api.main:app --host 127.0.0.1 --port 8000 --workers 1
Requires the state-store on :8081 and the stdio servers, which the McpTransport launches.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from mcp import StdioServerParameters
from pydantic import Field

from config import load_env
from contracts.common import CallContext, SessionId, Strict, TenantId, Tier, UserId
from contracts.errors import ErrorCode
from harness.executor import ToolExecutionHarness
from harness.llm_policy import make_policy
from harness.registry import Subagent
from harness.transport import McpTransport, Transport
from ingress.pipeline import Ingress
from mcp_servers.retrieval import Index
from mcp_servers.vault import load_vault
from plugins.loader import build_manager

ROOT = Path(__file__).resolve().parent.parent
load_env()  # .env at project root; real env vars take precedence
STATE_URL = os.environ.get("STATE_URL", "http://127.0.0.1:8081/mcp")


class QueryIn(Strict):
    tenant_id: TenantId
    user_id: UserId
    session_id: SessionId
    question: str = Field(min_length=1, max_length=2000)
    tier: Tier = "TIER_1_READ"


class DecideIn(Strict):
    tenant_id: TenantId
    user_id: UserId
    session_id: SessionId
    approval_id: str
    decision: Literal["APPROVE", "REJECT"]
    reason: str = Field(max_length=500)


def default_transport() -> McpTransport:
    py = os.environ.get("MCP_PYTHON", sys.executable)
    return McpTransport(
        stdio_cmds={
            "hybrid-rag": StdioServerParameters(command=py, args=["-m", "mcp_servers.rag_server"], cwd=str(ROOT)),
            "graphify-vault": StdioServerParameters(
                command=py, args=["-m", "mcp_servers.graphify_server"], cwd=str(ROOT)
            ),
        },
        http_urls={"state-store": STATE_URL},
    )


def create_app(transport: Transport | None = None, *, connect: bool = True) -> FastAPI:
    """Factory. Tests pass an InProcessTransport; the default uses real MCP transports."""
    holder: dict[str, Any] = {}

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        tr = transport or default_transport()
        if connect and isinstance(tr, McpTransport):
            await tr.connect()
        pm = build_manager()
        harness = ToolExecutionHarness(tr, pm=pm)
        holder["harness"] = harness
        holder["ingress"] = Ingress(harness, pm, Index.build(load_vault()), policy=make_policy())
        yield
        if isinstance(tr, McpTransport):
            await tr.close()

    app = FastAPI(title="The Unreliable Assistant (local PoC)", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/query")
    async def query(body: QueryIn) -> dict[str, Any]:
        ctx = CallContext(
            tenant_id=body.tenant_id,
            user_id=body.user_id,
            session_id=body.session_id,
            idempotency_key=uuid.uuid4(),
            tier=body.tier,
        )
        result: dict[str, Any] = await holder["ingress"].handle(ctx, body.question)
        return result

    @app.post("/approvals/decide")
    async def decide(body: DecideIn) -> dict[str, Any]:
        ctx = CallContext(
            tenant_id=body.tenant_id,
            user_id=body.user_id,
            session_id=body.session_id,
            idempotency_key=uuid.uuid4(),
            tier="TIER_3_MUTATE",
        )
        res = await holder["harness"].call(
            Subagent.APPROVER,
            "hitl_decide",
            ctx,
            {
                "ctx": ctx.model_dump(mode="json"),
                "approval_id": body.approval_id,
                "decision": body.decision,
                "reason": body.reason,
            },
        )
        if not res.ok:
            code = res.code or ErrorCode.INTERNAL_ERROR
            raise HTTPException(
                status_code=409 if code is ErrorCode.NOT_FOUND else 403,
                detail={"code": int(code), "name": code.name, "message": res.message},
            )
        return dict(res.data)

    @app.get("/audit/verify")
    async def audit_verify(tenant_id: TenantId, from_seq: int = 1, to_seq: int = 1000) -> dict[str, Any]:
        ctx = CallContext(
            tenant_id=tenant_id,
            user_id="user_audit",
            session_id="sess_audit0000",
            idempotency_key=uuid.uuid4(),
            tier="TIER_1_READ",
        )
        res = await holder["harness"].call(
            Subagent.VERIFICATION, "audit_verify", ctx, {"from_seq": from_seq, "to_seq": to_seq}
        )
        if not res.ok:
            raise HTTPException(status_code=403, detail={"code": int(res.code or 0), "message": res.message})
        return dict(res.data)

    return app


app = create_app()
