"""Transports from the harness to MCP servers.

- InProcessTransport: calls FastMCP tool functions directly (fast; used by chaos tests).
- McpTransport: real MCP ClientSession over stdio (hybrid-rag, graphify-vault) or
  streamable-HTTP (state-store). Used by the API and integration tests.
"""

from __future__ import annotations

import json
import re
from contextlib import AsyncExitStack
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Protocol

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import TextContent

from contracts.errors import ErrorCode, ToolError
from harness.registry import TOOLS

CODE_RE = re.compile(r"\[(-?\d+) (\w+)\]")


class TransportError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def code_from_text(text: str) -> ErrorCode:
    m = CODE_RE.search(text)
    if m:
        try:
            return ErrorCode(int(m.group(1)))
        except ValueError:
            pass
    return ErrorCode.INTERNAL_ERROR


class Transport(Protocol):
    async def call(self, tool: str, args: dict[str, Any], meta: dict[str, str]) -> dict[str, Any]: ...

    async def close(self) -> None: ...


@dataclass
class InProcessTransport:
    servers: dict[str, Any]  # server name -> FastMCP instance

    async def call(self, tool: str, args: dict[str, Any], meta: dict[str, str]) -> dict[str, Any]:
        spec = TOOLS[tool]
        server = self.servers[spec.server]
        fn = server._tool_manager.get_tool(tool).fn
        fake_ctx = SimpleNamespace(request_context=SimpleNamespace(meta=SimpleNamespace(**meta)))
        payload = spec.model.model_validate_json(json.dumps(args))
        kwargs: dict[str, Any] = {"payload": payload, "ctx": fake_ctx}
        try:
            out = fn(**kwargs)
        except ToolError as exc:
            raise TransportError(exc.code, exc.message) from exc
        return dict(out.model_dump(mode="json"))

    async def close(self) -> None:
        return None


class McpTransport:
    """Real MCP client. Sessions open lazily, one per server, and are reused."""

    def __init__(self, stdio_cmds: dict[str, StdioServerParameters], http_urls: dict[str, str]) -> None:
        self._stdio = stdio_cmds
        self._http = http_urls
        self._stack = AsyncExitStack()
        self._sessions: dict[str, ClientSession] = {}

    async def connect(self) -> None:
        """Open every session in the caller's task (anyio contexts must not cross tasks)."""
        for name in [*self._stdio, *self._http]:
            await self._session(name)

    async def _session(self, server: str) -> ClientSession:
        if server in self._sessions:
            return self._sessions[server]
        if server in self._stdio:
            r, w = await self._stack.enter_async_context(stdio_client(self._stdio[server]))
        else:
            r, w, _ = await self._stack.enter_async_context(streamable_http_client(self._http[server]))
        session = await self._stack.enter_async_context(ClientSession(r, w))
        await session.initialize()
        self._sessions[server] = session
        return session

    async def call(self, tool: str, args: dict[str, Any], meta: dict[str, str]) -> dict[str, Any]:
        server = TOOLS[tool].server
        session = self._sessions.get(server)
        if session is None:
            raise TransportError(ErrorCode.BACKEND_UNAVAILABLE, f"{server} not connected")
        result = await session.call_tool(tool, {"payload": args}, meta=meta)
        first = result.content[0] if result.content else None
        text = first.text if isinstance(first, TextContent) else ""
        if result.isError:
            raise TransportError(code_from_text(text), text)
        return dict(json.loads(text))

    async def close(self) -> None:
        await self._stack.aclose()
        self._sessions.clear()
