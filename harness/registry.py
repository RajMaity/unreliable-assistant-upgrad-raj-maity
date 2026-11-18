"""Tool registry, RBAC tiers and subagent bindings (design §1.2)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from contracts.common import Tier
from contracts.tools import (
    AuditAppendIn,
    AuditVerifyIn,
    CacheGetIn,
    CachePutIn,
    FormSubmitIn,
    HitlDecideIn,
    HybridRetrieveIn,
    ResolveAnchorIn,
    SessionGetFactsIn,
    SessionPutFactIn,
    SubgraphIn,
    WorkflowTransitionIn,
)

TIER_ORDER: dict[str, int] = {"TIER_1_READ": 1, "TIER_2_COMPUTE": 2, "TIER_3_MUTATE": 3}


@dataclass(frozen=True)
class ToolSpec:
    server: str
    tier: Tier
    model: type[BaseModel]


class Subagent(StrEnum):
    RETRIEVAL = "retrieval"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    TRIAGE = "triage"
    APPROVER = "approver"  # human approver role (hitl_decide only)


TOOLS: dict[str, ToolSpec] = {
    "hybrid_retrieve": ToolSpec("hybrid-rag", "TIER_1_READ", HybridRetrieveIn),
    "graph_resolve_anchor": ToolSpec("graphify-vault", "TIER_1_READ", ResolveAnchorIn),
    "graph_subgraph": ToolSpec("graphify-vault", "TIER_1_READ", SubgraphIn),
    "cache_get": ToolSpec("state-store", "TIER_1_READ", CacheGetIn),
    "cache_put": ToolSpec("state-store", "TIER_1_READ", CachePutIn),
    "session_get_facts": ToolSpec("state-store", "TIER_1_READ", SessionGetFactsIn),
    "session_put_fact": ToolSpec("state-store", "TIER_2_COMPUTE", SessionPutFactIn),
    "workflow_transition": ToolSpec("state-store", "TIER_3_MUTATE", WorkflowTransitionIn),
    "form_submit": ToolSpec("state-store", "TIER_3_MUTATE", FormSubmitIn),
    "hitl_decide": ToolSpec("state-store", "TIER_3_MUTATE", HitlDecideIn),
    "audit_append": ToolSpec("state-store", "TIER_1_READ", AuditAppendIn),
    "audit_verify": ToolSpec("state-store", "TIER_1_READ", AuditVerifyIn),
}

# design §1.2 subagent bindings. TRIAGE has no tools.
ALLOWED: dict[Subagent, frozenset[str]] = {
    Subagent.RETRIEVAL: frozenset({"hybrid_retrieve", "graph_resolve_anchor", "graph_subgraph"}),
    Subagent.EXECUTION: frozenset(
        {
            "cache_get",
            "cache_put",
            "session_get_facts",
            "session_put_fact",
            "workflow_transition",
            "form_submit",
        }
    ),
    Subagent.VERIFICATION: frozenset({"audit_append", "audit_verify", "session_get_facts"}),
    Subagent.TRIAGE: frozenset(),
    Subagent.APPROVER: frozenset({"hitl_decide"}),
}


def tier_allows(caller: str, required: str) -> bool:
    return TIER_ORDER[caller] >= TIER_ORDER[required]


def arg_payload(args: dict[str, Any]) -> dict[str, Any]:
    """Tool arguments without the ctx envelope (breaker key must not vary per call)."""
    return {k: v for k, v in args.items() if k != "ctx"}
