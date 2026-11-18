import os

# Offline by default: the suite never calls the LLM provider. Live tests opt in explicitly.
os.environ["LLM_BACKEND"] = "mock"
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

TENANT = "tenant_7"


def make_ctx(
    tenant: str = TENANT,
    user: str = "user_42",
    tier: str = "TIER_1_READ",
    key: str | None = None,
    session: str = "sess_ab12cd34ef",
) -> dict[str, Any]:
    return {
        "tenant_id": tenant,
        "user_id": user,
        "session_id": session,
        "idempotency_key": key or str(uuid.uuid4()),
        "tier": tier,
    }


def meta(tenant: str = TENANT) -> dict[str, str]:
    return {
        "tenant_id": tenant,
        "request_id": str(uuid.uuid4()),
        "traceparent": "00-" + "a" * 32 + "-" + "b" * 16 + "-01",
    }


@pytest.fixture
def vault():  # type: ignore[no-untyped-def]
    from mcp_servers.vault import load_vault

    return load_vault()


def ctx_model(**kw: object):  # type: ignore[no-untyped-def]
    """Build a CallContext the way the wire does: JSON-mode parse (strict UUID needs JSON)."""
    import json

    from contracts.common import CallContext

    return CallContext.model_validate_json(json.dumps(make_ctx(**kw)))  # type: ignore[arg-type]


@pytest.fixture
def stack(tmp_path):  # type: ignore[no-untyped-def]
    """All three MCP servers in-process, sharing one tmp SQLite state store."""
    import fakeredis

    from mcp_servers import graphify_server, rag_server
    from mcp_servers.state_store import Cache, StateService, Store, build_server
    from mcp_servers.vault import load_vault

    vault = load_vault()
    svc = StateService(Store(str(tmp_path / "state.sqlite3")), Cache(fakeredis.FakeRedis(decode_responses=True)))
    servers = {
        "hybrid-rag": rag_server.build_server(vault),
        "graphify-vault": graphify_server.build_server(vault),
        "state-store": build_server(svc),
    }
    return SimpleNamespace(servers=servers, svc=svc)
