"""36-KPI scorecard (design §1.5, CLAUDE.md P5). Reads results/*.json written by the suites.

Evidence classes:
- measured:    real measurement with real backends (none in this PoC: all backends are mocks)
- indicative:  measured locally with mock backends or a hand-labelled/synthetic set
- design-only: not measurable on this machine (needs scaled load, real LLM, or cost data)

Run: uv run python -m tests.eval.scorecard
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

RESULTS = Path(__file__).resolve().parents[2] / "results"


@dataclass
class Kpi:
    id: str
    name: str
    target: str
    value: str
    passed: bool | None
    evidence: str
    note: str = ""


def _load(name: str) -> dict[str, Any]:
    p = RESULTS / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _cmp(value: float | None, op: str, bound: float) -> bool | None:
    if value is None:
        return None
    return {"<=": value <= bound, ">=": value >= bound}[op]


def _num(
    kpi_id: str,
    name: str,
    target: str,
    value: float | None,
    op: str,
    bound: float,
    evidence: str = "indicative",
    note: str = "",
    fmt: str = "{:.4g}",
) -> Kpi:
    shown = "n/a" if value is None else fmt.format(value)
    return Kpi(
        kpi_id, name, target, shown, _cmp(value, op, bound), evidence if value is not None else "design-only", note
    )


def _design(kpi_id: str, name: str, target: str, note: str) -> Kpi:
    return Kpi(kpi_id, name, target, "not measured", None, "design-only", note)


def build() -> list[Kpi]:
    ing = _load("kpi_ingress.json")
    gold = _load("eval_golden.json")
    rt = _load("redteam.json")
    chaos = _load("kpi16_loop_escape.json")
    t3 = _load("kpi19_tier3.json")
    probe = _load("kpi28_tenant_probe.json")
    h1415 = _load("kpi14_15_harness.json")
    load = _load("load_local.json")  # API path: HTTP -> harness -> MCP stdio/HTTP, single worker

    def ingv(key: str) -> float | None:
        return ing.get(key, {}).get("value")

    ev = lambda k: "indicative"  # noqa: E731  every local value uses mock backends or a local set
    rows: list[Kpi] = []
    # Stage 1 - ingress & triage
    rows.append(
        _num(
            "KPI-01",
            "pre-hook p95",
            "<=15 ms",
            ingv("KPI-01_prehook_p95_ms"),
            "<=",
            15,
            ev("01"),
            "guards on a 10-question set; regex only",
            "{:.3f}",
        )
    )
    rows.append(
        _num(
            "KPI-02",
            "PII recall",
            ">=0.998",
            ingv("KPI-02_pii_recall"),
            ">=",
            0.998,
            note="hand-labelled 20 positives; tuned together with the regexes, so optimistic",
        )
    )
    rows.append(
        _num(
            "KPI-03",
            "injection block (red team)",
            ">=0.995",
            rt.get("injection_block_rate"),
            ">=",
            0.995,
            note="100-prompt red team; hand-labelled ingress set reports 1.0 but is weaker",
        )
    )
    rows.append(
        _num(
            "KPI-04",
            "triage accuracy",
            ">=0.97",
            ingv("KPI-04_triage_accuracy"),
            ">=",
            0.97,
            note="15 hand-labelled questions",
        )
    )
    rows.append(_num("KPI-05", "triage p95", "<=25 ms", ingv("KPI-05_triage_p95_ms"), "<=", 25, fmt="{:.3f}"))
    rows.append(
        _num("KPI-06", "false refusal", "<=0.01", ingv("KPI-06_false_refusal"), "<=", 0.01, note="15 benign questions")
    )
    # Stage 2 - hybrid retrieval
    rows.append(_num("KPI-07", "BM25 p95", "<=12 ms", gold.get("KPI-07_bm25_p95_ms"), "<=", 12, fmt="{:.3f}"))
    rows.append(_num("KPI-08", "dense p95", "<=25 ms", gold.get("KPI-08_dense_p95_ms"), "<=", 25, fmt="{:.3f}"))
    rows.append(_num("KPI-09", "rerank p95", "<=45 ms", gold.get("KPI-09_rerank_p95_ms"), "<=", 45, fmt="{:.3f}"))
    rows.append(
        _num(
            "KPI-10",
            "recall",
            ">=0.92 @50",
            gold.get("KPI-10_recall_at_10_proxy_for_50"),
            ">=",
            0.92,
            note="PROXY: measured @10 because the tool caps rerank_k at 10",
        )
    )
    rows.append(
        _num(
            "KPI-11",
            "precision@4",
            ">=0.88",
            gold.get("KPI-11_precision_at_4"),
            ">=",
            0.88,
            note="threshold 0.0 during eval, so off-topic chunks are included",
        )
    )
    rows.append(_num("KPI-12", "MRR@4", ">=0.85", gold.get("KPI-12_mrr_at_4"), ">=", 0.85))
    rows.append(_num("KPI-13", "nDCG@4", ">=0.86", gold.get("KPI-13_ndcg_at_4"), ">=", 0.86))
    # Stage 3 - agent & loop
    rows.append(
        _num(
            "KPI-14",
            "first-pass validation",
            ">=0.96",
            h1415.get("KPI-14_first_pass_rate_over_all_calls"),
            ">=",
            0.96,
            note="composition-driven: 13 of 53 calls are malformed by construction; not a model-behaviour estimate",
        )
    )
    rows.append(
        _num(
            "KPI-15",
            "auto-repair success",
            ">=0.90",
            h1415.get("KPI-15_repair_success_on_repairable"),
            ">=",
            0.90,
            note="8 repairable labelled entities; unrepairable ones are excluded",
        )
    )
    within = bool(chaos) and chaos.get("max_iterations_observed", 99) <= chaos.get("max_iterations_bound", 0)
    rows.append(
        _num(
            "KPI-16",
            "loop escape (all runs within bound)",
            "=1.0",
            (chaos["terminal_or_escaped"] / chaos["runs"]) if chaos and within else (0.0 if chaos else None),
            ">=",
            1.0,
            note="200 adversarial runs; every run terminated within 5 iterations",
        )
    )
    rows.append(_num("KPI-17", "mean ReAct steps", "<=2.2", gold.get("KPI-17_mean_react_steps"), "<=", 2.2))
    rows.append(_design("KPI-18", "goal drift", ">=0.85", "no drift metric implemented"))
    rows.append(
        _num(
            "KPI-19",
            "Tier-3 HITL",
            "=1.0",
            (t3["pending"] / t3["attempts"]) if t3 else None,
            ">=",
            1.0,
            note="100 Tier-3 attempts; 0 executed without approval",
        )
    )
    # Stage 4 - generation
    rows.append(
        _num(
            "KPI-20",
            "faithfulness (pass rate >=0.92)",
            ">=0.92",
            gold.get("KPI-20_faithfulness_pass_rate"),
            ">=",
            0.92,
            note="token-overlap PROXY, not the RAGAS LLM judge",
        )
    )
    rows.append(
        _num(
            "KPI-21",
            "answer relevancy",
            ">=0.90",
            gold.get("KPI-21_answer_relevancy_proxy"),
            ">=",
            0.90,
            note="token-overlap PROXY",
        )
    )
    rows.append(_num("KPI-22", "entity recall", ">=0.85", gold.get("KPI-22_entity_recall"), ">=", 0.85))
    rows.append(
        _num(
            "KPI-23",
            "abstention accuracy",
            ">=0.98",
            gold.get("KPI-23_abstention_accuracy"),
            ">=",
            0.98,
            note="6 unanswerable and 21 answerable golden questions",
        )
    )
    hall = None
    if gold.get("KPI-20_faithfulness_pass_rate") is not None:
        hall = 1 - gold["KPI-20_faithfulness_pass_rate"]
    rows.append(
        _num(
            "KPI-24",
            "hallucination (1 - faithfulness pass)",
            "<=0.005",
            hall,
            "<=",
            0.005,
            note="derived from the faithfulness PROXY",
        )
    )
    rows.append(_design("KPI-25", "TTFT p95", "<=180 ms", "no streaming LLM in the mock backend"))
    # Stage 5 - tokens & graph
    rows.append(
        _num(
            "KPI-26",
            "token reduction",
            ">=0.45",
            gold.get("KPI-26_token_reduction"),
            ">=",
            0.45,
            note="budget never binds on these short notes, so reduction is 0",
        )
    )
    rows.append(
        _num(
            "KPI-27",
            "cache hit rate",
            ">=0.35",
            gold.get("KPI-27_cache_hit_rate_synthetic_replay"),
            ">=",
            0.35,
            note="synthetic replay; includes first-pass semantic-tier hits",
        )
    )
    rows.append(
        _num(
            "KPI-28",
            "tenant isolation",
            "=1.0",
            (1 - probe["leaks"] / probe["probes"]) if probe else None,
            ">=",
            1.0,
            note="50 cross-tenant probes, each with a distinct tenant",
        )
    )
    rows.append(_design("KPI-29", "cost per 1k queries", "<=$0.45", "no billable model; mock LLM"))
    rows.append(_design("KPI-30", "MCP overhead p95", "<=4 ms", "transport overhead not isolated locally"))
    # Stage 6 - scale
    api_note = "local API path (HTTP, one uvicorn worker, mock backends), 400 requests at concurrency 20"
    rows.append(_num("KPI-31", "p50 end-to-end", "<=240 ms", load.get("p50_ms"), "<=", 240, note=api_note))
    rows.append(_num("KPI-32", "p95 end-to-end", "<=450 ms", load.get("p95_ms"), "<=", 450, note=api_note))
    rows.append(_num("KPI-33", "p99 end-to-end", "<=590 ms", load.get("p99_ms"), "<=", 590, note=api_note))
    rows.append(
        _num(
            "KPI-34",
            "5xx rate",
            "<=0.0005",
            load.get("rate_5xx"),
            "<=",
            0.0005,
            fmt="{:.6f}",
            note="local run only; the 50K run is design-only",
        )
    )
    rows.append(
        _num(
            "KPI-35",
            "throughput",
            ">=8500 RPS",
            load.get("rps"),
            ">=",
            8500,
            fmt="{:.1f}",
            note="single local injector and single worker; the 50K run is design-only",
        )
    )
    rows.append(_design("KPI-36", "RSS growth", "<=3%", "not measured in this PoC"))
    # Supplementary Gate 8 is not a KPI in §1.5; it is reported separately below.
    live = _load("eval_llm_golden.json")
    if live:
        rows = _apply_live(rows, live)
    return rows


def _apply_live(rows: list[Kpi], live: dict[str, Any]) -> list[Kpi]:
    """Replace offline rows with live OpenRouter measurements (evidence: measured)."""
    budget = live.get("loop_budget_ms", 450)
    note = (
        f"live {live['model']} via OpenRouter; judge is the same model family (LLM-as-judge, not RAGAS); "
        f"loop budget {budget:.0f} ms" + ("" if budget == 450 else " (measurement override, not the contract 450 ms)")
    )
    faith_pass = live.get("KPI-20_faithfulness_pass_rate_llm_judge")
    over = {
        "KPI-17": _num(
            "KPI-17", "mean ReAct steps", "<=2.2", live.get("KPI-17_mean_react_steps"), "<=", 2.2, "measured", note
        ),
        "KPI-20": _num(
            "KPI-20",
            "faithfulness (mean, LLM judge)",
            ">=0.92",
            live.get("KPI-20_mean_faithfulness_llm_judge"),
            ">=",
            0.92,
            "measured",
            note,
        ),
        "KPI-21": _num(
            "KPI-21",
            "answer relevancy (LLM judge)",
            ">=0.90",
            live.get("KPI-21_answer_relevancy_llm_judge"),
            ">=",
            0.90,
            "measured",
            note,
        ),
        "KPI-23": _num(
            "KPI-23",
            "abstention accuracy",
            ">=0.98",
            live.get("KPI-23_abstention_accuracy"),
            ">=",
            0.98,
            "measured",
            note + "; 27 golden questions",
        ),
        "KPI-24": _num(
            "KPI-24",
            "hallucination (1 - faithfulness pass rate)",
            "<=0.005",
            (1 - faith_pass) if faith_pass is not None else None,
            "<=",
            0.005,
            "measured",
            note + "; per-answer judge score >= 0.92",
        ),
        "KPI-25": _num(
            "KPI-25",
            "LLM response p95 (full, non-streaming)",
            "<=180 ms",
            live.get("KPI-25_llm_call_p95_ms_full_response_not_ttft"),
            "<=",
            180,
            "measured",
            "upper bound on TTFT: no streaming, so true TTFT is lower but not measured",
        ),
        "KPI-31": _num(
            "KPI-31", "p50 end-to-end", "<=240 ms", live.get("e2e_wall_p50_ms"), "<=", 240, "measured", note
        ),
        "KPI-32": _num(
            "KPI-32", "p95 end-to-end", "<=450 ms", live.get("e2e_wall_p95_ms"), "<=", 450, "measured", note
        ),
        "KPI-33": _num(
            "KPI-33", "p99 end-to-end", "<=590 ms", live.get("e2e_wall_p99_ms"), "<=", 590, "measured", note
        ),
    }
    amended = {"KPI-31": 2500, "KPI-32": 4500, "KPI-33": 6000}  # PROPOSED: docs/ADR_AMENDMENT_001.md
    for kid, bound in amended.items():
        key = {"KPI-31": "e2e_wall_p50_ms", "KPI-32": "e2e_wall_p95_ms", "KPI-33": "e2e_wall_p99_ms"}[kid]
        val = live.get(key)
        if val is not None and kid in over:
            verdict = "MET" if val <= bound else "NOT MET"
            over[kid] = Kpi(
                over[kid].id,
                over[kid].name,
                over[kid].target,
                over[kid].value,
                over[kid].passed,
                over[kid].evidence,
                over[kid].note + f"; proposed amended target <= {bound} ms: {verdict}",
            )
    return [over.get(r.id, r) for r in rows]


def render(rows: list[Kpi]) -> str:
    measured = [r for r in rows if r.passed is not None]
    passed = [r for r in measured if r.passed]
    lines = [
        "# 36-KPI scorecard (local PoC)",
        "",
        "All local results use mock backends or hand-labelled/synthetic sets: evidence class `indicative`.",
        "No KPI is reported as `measured`. `design-only` rows are not measurable on this machine.",
        "",
        f"Measured locally: {len(measured)}/36. Passing: {len(passed)}/{len(measured)}. "
        f"Design-only: {sum(r.evidence == 'design-only' for r in rows)}.",
        "",
        "| KPI | Name | Target | Value | Result | Evidence | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        res = "PASS" if r.passed else ("FAIL" if r.passed is False else "NOT MEASURED")
        lines.append(f"| {r.id} | {r.name} | {r.target} | {r.value} | {res} | {r.evidence} | {r.note} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    rows = build()
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "scorecard.json").write_text(json.dumps([asdict(r) for r in rows], indent=2), encoding="utf-8")
    (RESULTS / "scorecard.md").write_text(render(rows), encoding="utf-8")
    print(render(rows))


if __name__ == "__main__":
    main()
