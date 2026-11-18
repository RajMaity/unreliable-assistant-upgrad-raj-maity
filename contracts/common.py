"""Shared Pydantic v2 types. Source: design.md §1.2 (locked contract)."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

TenantId = Annotated[str, StringConstraints(pattern=r"^tenant_[A-Za-z0-9]{1,32}$")]
UserId = Annotated[str, StringConstraints(pattern=r"^user_[A-Za-z0-9]{1,48}$")]
SessionId = Annotated[str, StringConstraints(pattern=r"^sess_[A-Za-z0-9-]{8,40}$")]
SKU = Annotated[str, StringConstraints(pattern=r"^SKU-\d{3}$")]
LOT = Annotated[str, StringConstraints(pattern=r"^LOT-\d{5}$")]
ChunkId = Annotated[str, StringConstraints(pattern=r"^chk_[0-9a-f]{16}$")]
NoteId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9-]{2,63}$")]
Tier = Literal["TIER_1_READ", "TIER_2_COMPUTE", "TIER_3_MUTATE"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class CallContext(Strict):
    tenant_id: TenantId
    user_id: UserId
    session_id: SessionId
    # UUIDv4, required on TIER_2/3. strict=False on this field only: strict Python-mode
    # validation rejects the JSON string wire form. Pending ADR (see design deviation note).
    idempotency_key: Annotated[UUID, Field(strict=False)]
    tier: Tier


__all__ = [
    "SKU",
    "LOT",
    "CallContext",
    "ChunkId",
    "NoteId",
    "SessionId",
    "Strict",
    "TenantId",
    "Tier",
    "UserId",
]
