import json
import uuid

import pytest
from pydantic import ValidationError

from contracts.common import CallContext

VALID = {
    "tenant_id": "tenant_7",
    "user_id": "user_42",
    "session_id": "sess_ab12cd34ef",
    "idempotency_key": str(uuid.uuid4()),
    "tier": "TIER_1_READ",
}


def test_valid_context_parses_from_json() -> None:
    ctx = CallContext.model_validate_json(json.dumps(VALID))
    assert ctx.tenant_id == "tenant_7"
    assert ctx.tier == "TIER_1_READ"


def test_context_is_frozen() -> None:
    ctx = CallContext.model_validate_json(json.dumps(VALID))
    with pytest.raises(ValidationError):
        ctx.tier = "TIER_3_MUTATE"  # type: ignore[misc]


def test_extra_fields_forbidden() -> None:
    with pytest.raises(ValidationError):
        CallContext.model_validate_json(json.dumps({**VALID, "role": "admin"}))


@pytest.mark.parametrize(
    "field,bad",
    [
        ("tenant_id", "Tenant_7"),  # uppercase prefix: must be repaired by R4, not accepted raw
        ("user_id", "user-42"),
        ("session_id", "sess_short"),
        ("tier", "TIER_9"),
        ("idempotency_key", "not-a-uuid"),
    ],
)
def test_invalid_fields_rejected(field: str, bad: str) -> None:
    with pytest.raises(ValidationError):
        CallContext.model_validate_json(json.dumps({**VALID, field: bad}))
