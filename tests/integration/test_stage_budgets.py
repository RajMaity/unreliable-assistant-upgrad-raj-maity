"""design §1.4 per-stage p95 budgets, measured locally on the mock backends.

Local numbers are indicative (mock backends, single process), never measured-at-scale.
"""

import statistics

from contracts.tools import HybridRetrieveIn
from mcp_servers.retrieval import Index, hybrid_retrieve
from tests.conftest import ctx_model

BUDGET_MS = {"bm25": 25.0, "dense": 25.0, "rrf": 3.0, "rerank": 45.0}
RUNS = 200


def _p95(xs: list[float]) -> float:
    return statistics.quantiles(xs, n=20)[18]


def test_stage_p95_within_budget(vault) -> None:  # type: ignore[no-untyped-def]
    index = Index.build(vault)
    req = HybridRetrieveIn(ctx=ctx_model(), query="LOT-88392 safety protocol SKU-204 relay")
    samples: dict[str, list[float]] = {k: [] for k in BUDGET_MS}
    for _ in range(RUNS):
        out = hybrid_retrieve(index, req)
        for k in BUDGET_MS:
            samples[k].append(out.stage_ms[k])  # type: ignore[index]
    for stage, budget in BUDGET_MS.items():
        assert _p95(samples[stage]) <= budget, f"{stage} p95 over budget"
