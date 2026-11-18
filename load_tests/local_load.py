"""Local scaled-proxy load runner (single injector, single API worker).

Not the 50K design run. Results are INDICATIVE and go to results/load_local.json.
Run (API on :8000):  uv run python -m load_tests.local_load --concurrency 20 --requests 400
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from pathlib import Path

import httpx

QUESTIONS = [
    "What is the safety protocol for LOT-88392?",
    "What is the storage condition for LOT-00044?",
    "How often is SKU-099 inspected?",
    "What is the returns policy?",
    "Which shift runs late?",
]
OUT = Path(__file__).resolve().parents[1] / "results" / "load_local.json"


async def _worker(
    client: httpx.AsyncClient, base: str, n: int, i: int, latencies: list[float], codes: list[int]
) -> None:
    body = {
        "tenant_id": "tenant_7",
        "user_id": f"user_{i % 50}",
        "session_id": "sess_ab12cd34ef",
        "question": QUESTIONS[i % len(QUESTIONS)],
        "tier": "TIER_1_READ",
    }
    t0 = time.perf_counter()
    try:
        r = await client.post(f"{base}/query", json=body)
        codes.append(r.status_code)
    except httpx.HTTPError:
        codes.append(599)
    latencies.append((time.perf_counter() - t0) * 1000)


async def run(base: str, concurrency: int, requests: int) -> dict[str, object]:
    latencies: list[float] = []
    codes: list[int] = []
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(timeout=10.0) as client:

        async def guarded(i: int) -> None:
            async with sem:
                await _worker(client, base, requests, i, latencies, codes)

        t0 = time.perf_counter()
        await asyncio.gather(*(guarded(i) for i in range(requests)))
        wall = time.perf_counter() - t0
    ordered = sorted(latencies)

    def pct(p: int) -> float:
        return round(ordered[min(len(ordered) - 1, max(0, round(p / 100 * len(ordered)) - 1))], 3)

    five_xx = sum(1 for c in codes if c >= 500)
    return {
        "evidence_class": "indicative",
        "note": "single local injector, single API worker, mock backends; not the 50K design run",
        "concurrency": concurrency,
        "requests": requests,
        "rps": round(requests / wall, 3),
        "p50_ms": pct(50),
        "p95_ms": pct(95),
        "p99_ms": pct(99),
        "mean_ms": round(statistics.fmean(ordered), 3),
        "rate_5xx": round(five_xx / requests, 6),
        "KPI-34_target": "<=0.0005",
        "KPI-35_target": ">=8500 RPS (design-only at 50K)",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--concurrency", type=int, default=20)
    ap.add_argument("--requests", type=int, default=400)
    args = ap.parse_args()
    result = asyncio.run(run(args.base, args.concurrency, args.requests))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
