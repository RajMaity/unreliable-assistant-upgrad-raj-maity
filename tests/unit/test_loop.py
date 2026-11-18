from harness.executor import ToolExecutionHarness
from harness.loop import MAX_ITERATIONS, MockPolicy, ReActLoop
from harness.transport import InProcessTransport
from tests.conftest import ctx_model


def loop(stack) -> ReActLoop:  # type: ignore[no-untyped-def]
    return ReActLoop(ToolExecutionHarness(InProcessTransport(stack.servers), timeout_s=5.0))


async def test_grounded_question_is_answered(stack) -> None:  # type: ignore[no-untyped-def]
    res = await loop(stack).run("What is the safety protocol for LOT-88392?", ctx_model(), MockPolicy())
    assert res.status == "ANSWERED"
    assert res.citations
    assert res.iterations <= MAX_ITERATIONS


async def test_unrelated_question_abstains(stack) -> None:  # type: ignore[no-untyped-def]
    res = await loop(stack).run("Explain quantum chromodynamics.", ctx_model(), MockPolicy())
    assert res.status == "ABSTAINED"


async def test_tier3_request_pauses_for_approval(stack) -> None:  # type: ignore[no-untyped-def]
    c = ctx_model(tier="TIER_3_MUTATE")
    res = await loop(stack).run("Release LOT-00044 from hold", c, MockPolicy())
    assert res.status == "PENDING_APPROVAL"
    assert stack.svc.store.execute("SELECT COUNT(*) AS n FROM executed_actions")[0]["n"] == 0
