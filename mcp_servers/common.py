"""Helpers shared by all MCP servers: _meta tenant check and key validation."""

from typing import Any
from uuid import UUID

from mcp.server.fastmcp import Context

from contracts.common import CallContext
from contracts.errors import ErrorCode, ToolError


def check_meta(ctx: Context[Any, Any, Any], tenant_id: str) -> None:
    """design §1.2: every call carries _meta; its tenant must equal ctx.tenant_id."""
    meta: Any
    try:
        meta = ctx.request_context.meta
    except ValueError:  # not inside a request
        meta = None
    if meta is None or getattr(meta, "tenant_id", None) is None:
        raise ToolError(ErrorCode.INVALID_REQUEST, "_meta.tenant_id missing")
    if meta.tenant_id != tenant_id:
        raise ToolError(ErrorCode.TENANT_MISMATCH, "ctx.tenant_id differs from _meta.tenant_id")


def require_uuid4(ctx: CallContext) -> UUID:
    """UUIDv4 idempotency key is required on TIER_2 / TIER_3 calls."""
    if ctx.tier == "TIER_1_READ":
        return ctx.idempotency_key
    if ctx.idempotency_key.version != 4:
        raise ToolError(ErrorCode.VALIDATION_FAILED, "idempotency_key must be UUIDv4")
    return ctx.idempotency_key
