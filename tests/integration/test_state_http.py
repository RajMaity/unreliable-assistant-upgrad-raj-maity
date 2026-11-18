import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client as streamablehttp_client

from tests.conftest import make_ctx, meta

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def server_url(tmp_path_factory) -> Iterator[str]:  # type: ignore[no-untyped-def]
    port = _free_port()
    env = {**os.environ, "STATE_DB": str(tmp_path_factory.mktemp("db") / "state.sqlite3")}
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_servers.state_store", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.2)
    else:
        proc.kill()
        raise RuntimeError("state-store did not start")
    yield f"http://127.0.0.1:{port}/mcp"
    proc.terminate()
    proc.wait(timeout=10)


@pytest.mark.asyncio
async def test_cache_facts_approval_and_audit_over_http(server_url: str) -> None:
    async with streamablehttp_client(server_url) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            key = "c" * 64
            put = await s.call_tool(
                "cache_put",
                {
                    "payload": {
                        "ctx": make_ctx(),
                        "key": key,
                        "value": {"answer": "42"},
                        "ttl_s": 300,
                    }
                },
                meta=meta(),
            )
            assert json.loads(put.content[0].text)["stored"] is True
            got = json.loads(
                (await s.call_tool("cache_get", {"payload": {"ctx": make_ctx(), "key": key}}, meta=meta()))
                .content[0]
                .text
            )
            assert got["hit"] and got["value"] == {"answer": "42"}

            other = json.loads(
                (
                    await s.call_tool(
                        "cache_get",
                        {"payload": {"ctx": make_ctx(tenant="tenant_9"), "key": key}},
                        meta=meta("tenant_9"),
                    )
                )
                .content[0]
                .text
            )
            assert other["hit"] is False

            req = await s.call_tool(
                "workflow_transition",
                {
                    "payload": {
                        "ctx": make_ctx(tier="TIER_3_MUTATE"),
                        "entity": "LOT-00044",
                        "from_state": "held",
                        "to_state": "released",
                        "payload": {},
                    }
                },
                meta=meta(),
            )
            pending = json.loads(req.content[0].text)
            assert pending["status"] == "PENDING_APPROVAL"
            aid = pending["approval_id"]
            blocked = await s.call_tool(
                "hitl_decide",
                {
                    "payload": {
                        "ctx": make_ctx(tier="TIER_3_MUTATE"),
                        "approval_id": aid,
                        "decision": "APPROVE",
                        "reason": "self",
                    }
                },
                meta=meta(),
            )
            assert blocked.isError
            done = json.loads(
                (
                    await s.call_tool(
                        "hitl_decide",
                        {
                            "payload": {
                                "ctx": make_ctx(user="user_77"),
                                "approval_id": aid,
                                "decision": "APPROVE",
                                "reason": "reviewed",
                            }
                        },
                        meta=meta(),
                    )
                )
                .content[0]
                .text
            )
            assert done["status"] == "APPROVED"

            a = json.loads(
                (
                    await s.call_tool(
                        "audit_append",
                        {
                            "payload": {
                                "ctx": make_ctx(),
                                "event_type": "query",
                                "payload_redacted": {"n": 1},
                            }
                        },
                        meta=meta(),
                    )
                )
                .content[0]
                .text
            )
            assert a["seq"] >= 1
            v = json.loads(
                (
                    await s.call_tool(
                        "audit_verify",
                        {"payload": {"from_seq": 1, "to_seq": a["seq"]}},
                        meta=meta(),
                    )
                )
                .content[0]
                .text
            )
            assert v["valid"] is True
