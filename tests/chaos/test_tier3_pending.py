"""KPI-19: Tier-3 mutations are always PENDING_APPROVAL; nothing executes without a human."""

import json
import random
import uuid
from pathlib import Path

from contracts.errors import ErrorCode
from harness.executor import ToolExecutionHarness
from harness.registry import Subagent
from harness.transport import InProcessTransport
from tests.conftest import ctx_model


async def test_tier3_always_pending_and_never_executed(stack) -> None:  # type: ignore[no-untyped-def]
    rng = random.Random(99)
    harness = ToolExecutionHarness(InProcessTransport(stack.servers), timeout_s=5.0)
    attempts = 100
    for _ in range(attempts):
        c = ctx_model(tier="TIER_3_MUTATE", key=str(uuid.uuid4()), user=f"user_{rng.randint(1, 999)}")
        entity = rng.choice(["SKU-099", "LOT-00044", "sku 99", "lot 44"])
        r = await harness.call(
            Subagent.EXECUTION,
            "workflow_transition",
            c,
            {
                "ctx": c.model_dump(mode="json"),
                "entity": entity,
                "from_state": "held",
                "to_state": "released",
                "payload": {},
            },
        )
        assert r.code is ErrorCode.HITL_PENDING
    executed = stack.svc.store.execute("SELECT COUNT(*) AS n FROM executed_actions")[0]["n"]
    pending = stack.svc.store.execute("SELECT COUNT(*) AS n FROM approvals WHERE status='PENDING_APPROVAL'")[0]["n"]
    assert executed == 0
    assert pending == attempts  # KPI-19 (indicative: local mock)
    out = Path(__file__).resolve().parents[2] / "results" / "kpi19_tier3.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {"evidence_class": "indicative", "attempts": attempts, "pending": pending, "executed": executed}, indent=2
        ),
        encoding="utf-8",
    )
