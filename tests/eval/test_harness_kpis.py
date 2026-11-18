"""KPI-14 (first-pass validation) and KPI-15 (auto-repair success) from harness counters.

Labelled call set: well-formed calls, repairable malformed calls, unrepairable malformed calls.
Evidence class: INDICATIVE (small hand-labelled set; the mock policy emits well-formed calls).
"""

import json
from pathlib import Path

from contracts.common import CallContext
from harness.executor import ToolExecutionHarness
from harness.registry import Subagent
from harness.transport import InProcessTransport
from tests.conftest import ctx_model

OUT = Path(__file__).resolve().parents[2] / "results" / "kpi14_15_harness.json"


async def test_first_pass_and_repair_rates(stack) -> None:  # type: ignore[no-untyped-def]
    h = ToolExecutionHarness(InProcessTransport(stack.servers), timeout_s=5.0)
    c: CallContext = ctx_model()
    j = c.model_dump(mode="json")
    well_formed = 40
    for _ in range(well_formed):
        await h.call(Subagent.RETRIEVAL, "hybrid_retrieve", c, {"ctx": j, "query": "LOT-88392 safety"})
    repairable = ["sku 99", "sku_7", "SKU99", "sku-0099", "lot 44", "lot#44", "LOT:00044", "lot_88392"]
    for entity in repairable:
        await h.call(
            Subagent.EXECUTION,
            "workflow_transition",
            ctx_model(tier="TIER_3_MUTATE"),
            {
                "ctx": ctx_model(tier="TIER_3_MUTATE").model_dump(mode="json"),
                "entity": entity,
                "from_state": "a",
                "to_state": "b",
                "payload": {},
            },
        )
    unrepairable = ["SKU-1234", "LOT-123456", "ЅKU-099", "sku", "lot 1234567"]
    for entity in unrepairable:
        await h.call(
            Subagent.EXECUTION,
            "workflow_transition",
            ctx_model(tier="TIER_3_MUTATE"),
            {
                "ctx": ctx_model(tier="TIER_3_MUTATE").model_dump(mode="json"),
                "entity": entity,
                "from_state": "a",
                "to_state": "b",
                "payload": {},
            },
        )

    total_calls = well_formed + len(repairable) + len(unrepairable)
    first_pass_rate = h.stats.first_pass_ok / total_calls
    repair_success = h.stats.repaired_ok / len(repairable)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "evidence_class": "indicative",
                "KPI-14_first_pass_rate_over_all_calls": round(first_pass_rate, 6),
                "KPI-15_repair_success_on_repairable": round(repair_success, 6),
                "counts": {
                    "well_formed": well_formed,
                    "repairable": len(repairable),
                    "unrepairable": len(unrepairable),
                    "stats": h.stats.__dict__,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    assert repair_success == 1.0
