"""circuit_breaker (DISPATCH, priority 10, max 2): blocks identical calls after repeated failures."""

from __future__ import annotations

from typing import Any

from contracts.errors import ErrorCode, ToolError
from plugins.hookspecs import hookimpl


class CircuitBreaker:
    def __init__(self, max_failures: int = 2) -> None:
        self.max_failures = max_failures
        self.failures: dict[tuple[str, str], int] = {}

    @hookimpl(tryfirst=True)
    def tool_dispatch(self, call: dict[str, Any]) -> None:
        key = (call["tool"], call["args_hash"])
        if self.failures.get(key, 0) >= self.max_failures:
            raise ToolError(ErrorCode.BREAKER_OPEN, "breaker open for identical call")

    @hookimpl
    def post_execution(self, response: dict[str, Any]) -> None:
        if "args_hash" not in response or not response.get("failed"):
            return
        key = (response["tool"], response["args_hash"])
        self.failures[key] = self.failures.get(key, 0) + 1
