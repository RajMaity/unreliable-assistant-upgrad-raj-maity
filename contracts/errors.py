"""JSON-RPC error codes and retry policy. Source: design.md §1.2 'Error codes'."""

from enum import Enum, IntEnum


class ErrorCode(IntEnum):
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    TENANT_MISMATCH = -32001
    RBAC_DENIED = -32002
    VALIDATION_FAILED = -32003
    IDEMPOTENCY_CONFLICT = -32004
    BREAKER_OPEN = -32005
    HITL_PENDING = -32006
    TIMEOUT = -32007
    INJECTION_BLOCKED = -32008
    PII_BLOCKED = -32009
    NOT_FOUND = -32010
    BACKEND_UNAVAILABLE = -32011
    MANIFEST_INVALID = -32012


class Retry(Enum):
    NEVER = "no"
    AFTER_REPAIR = "after repair"
    ONCE = "once"
    POLL = "poll"
    ONCE_THEN_FALLBACK = "once, then fallback"
    FALLBACK = "fallback"


_POLICY: dict[ErrorCode, Retry] = {
    ErrorCode.PARSE_ERROR: Retry.NEVER,
    ErrorCode.INVALID_REQUEST: Retry.NEVER,
    ErrorCode.METHOD_NOT_FOUND: Retry.NEVER,
    ErrorCode.INVALID_PARAMS: Retry.AFTER_REPAIR,
    ErrorCode.INTERNAL_ERROR: Retry.ONCE,
    ErrorCode.TENANT_MISMATCH: Retry.NEVER,
    ErrorCode.RBAC_DENIED: Retry.NEVER,
    ErrorCode.VALIDATION_FAILED: Retry.NEVER,
    ErrorCode.IDEMPOTENCY_CONFLICT: Retry.NEVER,
    ErrorCode.BREAKER_OPEN: Retry.NEVER,
    ErrorCode.HITL_PENDING: Retry.POLL,
    ErrorCode.TIMEOUT: Retry.ONCE_THEN_FALLBACK,
    ErrorCode.INJECTION_BLOCKED: Retry.NEVER,
    ErrorCode.PII_BLOCKED: Retry.NEVER,
    ErrorCode.NOT_FOUND: Retry.NEVER,
    ErrorCode.BACKEND_UNAVAILABLE: Retry.FALLBACK,
    ErrorCode.MANIFEST_INVALID: Retry.NEVER,
}


def retry_policy(code: ErrorCode) -> Retry:
    return _POLICY[code]


class ToolError(Exception):
    """Raised by MCP servers; carries the design §1.2 JSON-RPC error code."""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(f"[{int(code)} {code.name}] {message}")
        self.code = code
        self.message = message
