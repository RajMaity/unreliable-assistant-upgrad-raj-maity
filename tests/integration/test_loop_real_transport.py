"""End-to-end: ReAct loop over real MCP transports (stdio rag/graph, streamable-HTTP state-store)."""

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from mcp import StdioServerParameters

from harness.executor import ToolExecutionHarness
from harness.loop import MockPolicy, ReActLoop
from harness.transport import McpTransport
from tests.conftest import ctx_model

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def state_url(tmp_path_factory) -> str:  # type: ignore[no-untyped-def]
    port = _free_port()
    env = {**os.environ, "STATE_DB": str(tmp_path_factory.mktemp("e2e") / "state.sqlite3")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_servers.state_store", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.2)
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(timeout=10)


def _stdio(module: str) -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", module], cwd=str(ROOT))


async def test_grounded_answer_over_real_transports(state_url: str) -> None:
    transport = McpTransport(
        stdio_cmds={
            "hybrid-rag": _stdio("mcp_servers.rag_server"),
            "graphify-vault": _stdio("mcp_servers.graphify_server"),
        },
        http_urls={"state-store": state_url},
    )
    await transport.connect()
    try:
        loop = ReActLoop(ToolExecutionHarness(transport, timeout_s=10.0))
        res = await loop.run("What is the safety protocol for LOT-88392?", ctx_model(), MockPolicy())
        assert res.status == "ANSWERED"
        assert res.citations
    finally:
        await transport.close()


async def test_tier3_pending_over_real_http(state_url: str) -> None:
    transport = McpTransport(
        stdio_cmds={
            "hybrid-rag": _stdio("mcp_servers.rag_server"),
            "graphify-vault": _stdio("mcp_servers.graphify_server"),
        },
        http_urls={"state-store": state_url},
    )
    await transport.connect()
    try:
        loop = ReActLoop(ToolExecutionHarness(transport, timeout_s=10.0))
        res = await loop.run("Release LOT-00044 from hold", ctx_model(tier="TIER_3_MUTATE"), MockPolicy())
        assert res.status == "PENDING_APPROVAL"
    finally:
        await transport.close()
