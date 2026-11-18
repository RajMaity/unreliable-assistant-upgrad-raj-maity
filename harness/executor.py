"""ToolExecutionHarness: validate → one deterministic repair → tenant → RBAC → idempotency
→ pluggy tool_dispatch (circuit breaker) → dispatch with timeout → post_execution.
Order per design §1.2 'Harness'.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

import pluggy
from pydantic import ValidationError

from contracts.common import CallContext
from contracts.errors import ErrorCode, ToolError
from contracts.repair import repair_lot, repair_sku, repair_tenant_id
from harness.registry import ALLOWED, TOOLS, Subagent, arg_payload, tier_allows
from harness.transport import Transport, TransportError
from mcp_servers.common import require_uuid4
from plugins.breaker import CircuitBreaker
from plugins.hookspecs import make_manager

BREAKER_THRESHOLD = 2
PER_CALL_TIMEOUT_S = 0.120  # design §1.4: per_step_timeout 120 ms


@dataclass
class ToolResult:
    ok: bool
    tool: str
    code: ErrorCode | None = None
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    repairs: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.ok:
            return "OK"
        if self.code is ErrorCode.HITL_PENDING:
            return "PENDING_APPROVAL"
        return self.code.name if self.code else "ERROR"


@dataclass
class HarnessStats:
    first_pass_ok: int = 0
    repaired_ok: int = 0
    repair_failed: int = 0


def _fail(tool: str, code: ErrorCode, message: str, repairs: list[str] | None = None) -> ToolResult:
    return ToolResult(False, tool, code, message, repairs=repairs or [])


def _repair_entity(value: str, repairs: list[str], label: str) -> str | None:
    fixed = repair_sku(value) or repair_lot(value)
    if fixed is None:
        return None
    if fixed.rule:
        repairs.append(f"{fixed.rule}:{label}")
    return fixed.value


class ToolExecutionHarness:
    def __init__(
        self,
        transport: Transport,
        timeout_s: float = PER_CALL_TIMEOUT_S,
        pm: pluggy.PluginManager | None = None,
    ) -> None:
        self.transport = transport
        self.timeout_s = timeout_s
        self.stats = HarnessStats()
        if pm is None:  # default: only the circuit breaker is active
            pm = make_manager()
            pm.register(CircuitBreaker(BREAKER_THRESHOLD), name="circuit_breaker")
        self.pm = pm

    # --- validation & repair ------------------------------------------------------
    @staticmethod
    def _repair(node: Any, repairs: list[str]) -> tuple[Any, bool]:
        """Apply R1–R4 to SKU/LOT/tenant fields only. Returns (node, unrepairable)."""
        if isinstance(node, list):
            out_list: list[Any] = []
            bad = False
            for item in node:
                child, child_bad = ToolExecutionHarness._repair(item, repairs)
                out_list.append(child)
                bad = bad or child_bad
            return out_list, bad
        if not isinstance(node, dict):
            return node, False
        out: dict[str, Any] = {}
        bad = False
        for key, value in node.items():
            if key == "entity" and isinstance(value, str):
                fixed = _repair_entity(value, repairs, key)
                bad = bad or fixed is None
                out[key] = value if fixed is None else fixed
            elif key == "entities" and isinstance(value, list):
                items: list[Any] = []
                for item in value:
                    if isinstance(item, str) and item.upper().startswith(("SKU", "LOT")):
                        fixed_s = _repair_entity(item, repairs, key)
                        bad = bad or fixed_s is None
                        items.append(item if fixed_s is None else fixed_s)
                    else:
                        items.append(item)
                out[key] = items
            elif key == "tenant_id" and isinstance(value, str):
                fixed_t = repair_tenant_id(value)
                bad = bad or fixed_t is None
                if fixed_t is not None and fixed_t.rule:
                    repairs.append(f"{fixed_t.rule}:{key}")
                out[key] = value if fixed_t is None else fixed_t.value
            else:
                child, child_bad = ToolExecutionHarness._repair(value, repairs)
                out[key] = child
                bad = bad or child_bad
        return out, bad

    # --- main entry ----------------------------------------------------------------
    async def call(self, subagent: Subagent, tool: str, ctx: CallContext, args: dict[str, Any]) -> ToolResult:
        spec = TOOLS.get(tool)
        if spec is None:
            return _fail(tool, ErrorCode.METHOD_NOT_FOUND, f"unknown tool {tool}")

        # (1) strict validation, (2) one deterministic repair, then re-validate
        repairs: list[str] = []
        try:
            model = spec.model.model_validate_json(json.dumps(args))
            self.stats.first_pass_ok += 1
        except ValidationError as first_error:
            fixed, unrepairable = self._repair(args, repairs)
            if unrepairable:
                self.stats.repair_failed += 1
                return _fail(tool, ErrorCode.VALIDATION_FAILED, "unrepairable SKU/LOT/tenant")
            if not repairs:
                return _fail(tool, ErrorCode.INVALID_PARAMS, str(first_error.errors()[0]["msg"]))
            try:
                model = spec.model.model_validate_json(json.dumps(fixed))
            except ValidationError as exc:
                self.stats.repair_failed += 1
                return _fail(tool, ErrorCode.VALIDATION_FAILED, str(exc.errors()[0]["msg"]), repairs)
            self.stats.repaired_ok += 1
            args = fixed

        # (3) tenant + RBAC
        req_ctx = getattr(model, "ctx", None)
        if isinstance(req_ctx, CallContext) and req_ctx.tenant_id != ctx.tenant_id:
            return _fail(tool, ErrorCode.TENANT_MISMATCH, "tenant differs from session", repairs)
        if tool not in ALLOWED[subagent]:
            return _fail(tool, ErrorCode.RBAC_DENIED, f"{subagent.value} may not call {tool}", repairs)
        if not tier_allows(ctx.tier, spec.tier):
            return _fail(tool, ErrorCode.RBAC_DENIED, f"tier {ctx.tier} < {spec.tier}", repairs)

        # (4) UUIDv4 idempotency on T2/T3
        if spec.tier != "TIER_1_READ":
            try:
                require_uuid4(ctx)
            except ToolError:
                return _fail(tool, ErrorCode.VALIDATION_FAILED, "UUIDv4 idempotency key required", repairs)

        # (5) pluggy tool_dispatch (circuit breaker)
        payload_hash = hashlib.sha256(json.dumps(arg_payload(args), sort_keys=True).encode()).hexdigest()
        call = {"tool": tool, "args_hash": payload_hash, "tenant_id": ctx.tenant_id}
        try:
            self.pm.hook.tool_dispatch(call=call)
        except ToolError as exc:
            return _fail(tool, exc.code, exc.message, repairs)

        # (6) dispatch with per-call timeout
        meta = {
            "tenant_id": ctx.tenant_id,
            "request_id": str(uuid.uuid4()),
            "traceparent": "00-" + uuid.uuid4().hex + "-" + uuid.uuid4().hex[:16] + "-01",
        }
        try:
            data = await asyncio.wait_for(self.transport.call(tool, args, meta), self.timeout_s)
        except TimeoutError:
            self._after(call, failed=True)
            return _fail(tool, ErrorCode.TIMEOUT, "per-call budget exceeded", repairs)
        except TransportError as exc:
            self._after(call, failed=exc.code is not ErrorCode.HITL_PENDING)
            return _fail(tool, exc.code, exc.message, repairs)
        self._after(call, failed=False)
        if data.get("status") == "PENDING_APPROVAL":
            return ToolResult(False, tool, ErrorCode.HITL_PENDING, "awaiting human approval", data, repairs)
        return ToolResult(True, tool, data=data, repairs=repairs)

    def _after(self, call: dict[str, Any], failed: bool) -> None:
        self.pm.hook.post_execution(response={**call, "failed": failed})
