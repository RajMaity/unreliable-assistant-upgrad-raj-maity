import pytest

from contracts.errors import ErrorCode, Retry, retry_policy


def test_all_design_error_codes_present() -> None:
    expected = {
        -32700: "PARSE_ERROR",
        -32600: "INVALID_REQUEST",
        -32601: "METHOD_NOT_FOUND",
        -32602: "INVALID_PARAMS",
        -32603: "INTERNAL_ERROR",
        -32001: "TENANT_MISMATCH",
        -32002: "RBAC_DENIED",
        -32003: "VALIDATION_FAILED",
        -32004: "IDEMPOTENCY_CONFLICT",
        -32005: "BREAKER_OPEN",
        -32006: "HITL_PENDING",
        -32007: "TIMEOUT",
        -32008: "INJECTION_BLOCKED",
        -32009: "PII_BLOCKED",
        -32010: "NOT_FOUND",
        -32011: "BACKEND_UNAVAILABLE",
        -32012: "MANIFEST_INVALID",
    }
    assert {int(c): c.name for c in ErrorCode} == expected


@pytest.mark.parametrize(
    "code,policy",
    [
        (ErrorCode.PARSE_ERROR, Retry.NEVER),
        (ErrorCode.INVALID_REQUEST, Retry.NEVER),
        (ErrorCode.METHOD_NOT_FOUND, Retry.NEVER),
        (ErrorCode.INVALID_PARAMS, Retry.AFTER_REPAIR),
        (ErrorCode.INTERNAL_ERROR, Retry.ONCE),
        (ErrorCode.TENANT_MISMATCH, Retry.NEVER),
        (ErrorCode.RBAC_DENIED, Retry.NEVER),
        (ErrorCode.VALIDATION_FAILED, Retry.NEVER),
        (ErrorCode.IDEMPOTENCY_CONFLICT, Retry.NEVER),
        (ErrorCode.BREAKER_OPEN, Retry.NEVER),
        (ErrorCode.HITL_PENDING, Retry.POLL),
        (ErrorCode.TIMEOUT, Retry.ONCE_THEN_FALLBACK),
        (ErrorCode.INJECTION_BLOCKED, Retry.NEVER),
        (ErrorCode.PII_BLOCKED, Retry.NEVER),
        (ErrorCode.NOT_FOUND, Retry.NEVER),
        (ErrorCode.BACKEND_UNAVAILABLE, Retry.FALLBACK),
        (ErrorCode.MANIFEST_INVALID, Retry.NEVER),
    ],
)
def test_retry_policy_matches_design(code: ErrorCode, policy: Retry) -> None:
    assert retry_policy(code) is policy
