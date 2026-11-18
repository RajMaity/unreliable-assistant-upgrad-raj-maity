"""Golden-set evaluation (design KPI-07..13, 17, 20..23, 26). Mock judge: INDICATIVE only.

Writes results/eval_golden.json, which tests/eval/scorecard.py reads.
Run: uv run pytest tests/eval -m ragas -q
"""

import json
import statistics
import time
from pathlib import Path

import pytest

from contracts.tools import HybridRetrieveIn, SubgraphIn
from harness.executor import ToolExecutionHarness
from harness.loop import MockPolicy, ReActLoop
from harness.transport import InProcessTransport
from mcp_servers.graphify_server import subgraph
from mcp_servers.retrieval import Index, hybrid_retrieve
from mcp_servers.vault import load_vault
from plugins.hookspecs import make_manager
from plugins.ragas import FAITHFULNESS_MIN, RagasAuditor, faithfulness_score
from tests.conftest import ctx_model
from tests.eval.metrics import (
    entity_recall,
    mrr_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    token_overlap,
)

GOLDEN = json.loads((Path(__file__).parent / "data" / "golden.json").read_text(encoding="utf-8"))
OUT = Path(__file__).resolve().parents[2] / "results" / "eval_golden.json"


def _p95(xs: list[float]) -> float:
    return round(statistics.quantiles(xs, n=20)[18], 4) if len(xs) >= 20 else round(max(xs), 4)


@pytest.mark.ragas
async def test_golden_set_metrics(stack, vault) -> None:  # type: ignore[no-untyped-def]
    index = Index.build(vault)
    ctx = ctx_model()
    pm = make_manager()
    pm.register(RagasAuditor(), name="ragas_auditor")
    harness = ToolExecutionHarness(InProcessTransport(stack.servers), timeout_s=5.0, pm=pm)
    loop = ReActLoop(harness)

    p4: list[float] = []
    r10: list[float] = []
    mrr: list[float] = []
    ndcg: list[float] = []
    ent: list[float] = []
    rel: list[float] = []
    faith_ok: list[bool] = []
    abst_ok: list[bool] = []
    steps: list[int] = []
    stage: dict[str, list[float]] = {"bm25": [], "dense": [], "rerank": []}
    e2e_ms: list[float] = []

    answerable = [g for g in GOLDEN if g["answerable"]]
    for g in answerable:
        out = hybrid_retrieve(index, HybridRetrieveIn(ctx=ctx, query=g["q"], rerank_k=10, threshold=0.0))
        for k in stage:
            stage[k].append(out.stage_ms[k])  # type: ignore[index]
        flags = [c.note_id in g["notes"] for c in out.chunks]
        total = len(g["notes"])
        p4.append(precision_at_k(flags, 4))
        r10.append(recall_at_k(flags, total, 10))
        mrr.append(mrr_at_k(flags, 4))
        ndcg.append(ndcg_at_k(flags, total, 4))

        t0 = time.perf_counter()
        res = await loop.run(g["q"], ctx, MockPolicy())
        e2e_ms.append((time.perf_counter() - t0) * 1000)
        steps.append(res.iterations)
        ent.append(entity_recall(g["entities"], res.answer + " " + " ".join(res.context)))
        rel.append(token_overlap(g["q"], res.answer))
        answered = res.status == "ANSWERED"
        faith_ok.append(answered and faithfulness_score(res.answer, list(res.context)) >= FAITHFULNESS_MIN)
        abst_ok.append(answered)

    for g in (x for x in GOLDEN if not x["answerable"]):
        res = await loop.run(g["q"], ctx, MockPolicy())
        abst_ok.append(res.status == "ABSTAINED")

    tokens = raw = 0
    for nid in vault.notes:
        sg = subgraph(vault, SubgraphIn(ctx=ctx, anchor_note_id=nid, depth=2, token_budget=320))
        tokens += sg.tokens
        raw += sg.raw_tokens_est

    report = {
        "evidence_class": "indicative",
        "judge": "mock: token-overlap proxies, not the RAGAS LLM judge",
        "n_answerable": len(answerable),
        "n_unanswerable": len(GOLDEN) - len(answerable),
        "KPI-07_bm25_p95_ms": _p95(stage["bm25"]),
        "KPI-08_dense_p95_ms": _p95(stage["dense"]),
        "KPI-09_rerank_p95_ms": _p95(stage["rerank"]),
        "e2e_loop_p50_ms": _pct(e2e_ms, 50),
        "e2e_loop_p95_ms": _pct(e2e_ms, 95),
        "e2e_loop_p99_ms": _pct(e2e_ms, 99),
        "KPI-10_recall_at_10_proxy_for_50": _mean([float(x) for x in r10]),
        "KPI-11_precision_at_4": _mean(p4),
        "KPI-12_mrr_at_4": _mean(mrr),
        "KPI-13_ndcg_at_4": _mean(ndcg),
        "KPI-17_mean_react_steps": _mean([float(s) for s in steps]),
        "KPI-20_faithfulness_pass_rate": _mean([float(x) for x in faith_ok]),
        "KPI-21_answer_relevancy_proxy": _mean(rel),
        "KPI-22_entity_recall": _mean(ent),
        "KPI-23_abstention_accuracy": _mean([float(x) for x in abst_ok]),
        "KPI-26_token_reduction": round(1 - tokens / raw, 6) if raw else 0.0,
        "raw_subgraph_tokens": raw,
        "compressed_subgraph_tokens": tokens,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    await _cache_replay(stack, ctx)


def _pct(xs: list[float], p: int) -> float:
    ordered = sorted(xs)
    idx = min(len(ordered) - 1, max(0, round(p / 100 * len(ordered)) - 1))
    return round(ordered[idx], 4)


def _mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 6) if xs else 0.0


async def _cache_replay(stack, ctx) -> None:  # type: ignore[no-untyped-def]
    """KPI-27 on a synthetic replay: each answerable question asked twice in one tenant."""
    from ingress.pipeline import Ingress
    from plugins.hookspecs import make_manager as _mm

    pm = _mm()
    harness = ToolExecutionHarness(InProcessTransport(stack.servers), timeout_s=5.0, pm=pm)
    ing = Ingress(harness, pm, Index.build(load_vault()))
    qs = [g["q"] for g in GOLDEN if g["answerable"]]
    hits = total = 0
    for q in qs * 2:
        res = await ing.handle(ctx, q)
        total += 1
        hits += int(res["cache_hit"])
    data = json.loads(OUT.read_text(encoding="utf-8"))
    data["KPI-27_cache_hit_rate_synthetic_replay"] = round(hits / total, 6)
    data["KPI-27_note"] = "two passes over the golden set; the second pass is the replay"
    OUT.write_text(json.dumps(data, indent=2), encoding="utf-8")
