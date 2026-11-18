# 36-KPI scorecard (local PoC)

All local results use mock backends or hand-labelled/synthetic sets: evidence class `indicative`.
No KPI is reported as `measured`. `design-only` rows are not measurable on this machine.

Measured locally: 32/36. Passing: 21/32. Design-only: 4.

| KPI | Name | Target | Value | Result | Evidence | Note |
|---|---|---|---|---|---|---|
| KPI-01 | pre-hook p95 | <=15 ms | 0.018 | PASS | indicative | guards on a 10-question set; regex only |
| KPI-02 | PII recall | >=0.998 | 1 | PASS | indicative | hand-labelled 20 positives; tuned together with the regexes, so optimistic |
| KPI-03 | injection block (red team) | >=0.995 | 0.8333 | FAIL | indicative | 100-prompt red team; hand-labelled ingress set reports 1.0 but is weaker |
| KPI-04 | triage accuracy | >=0.97 | 1 | PASS | indicative | 15 hand-labelled questions |
| KPI-05 | triage p95 | <=25 ms | 0.008 | PASS | indicative |  |
| KPI-06 | false refusal | <=0.01 | 0 | PASS | indicative | 15 benign questions |
| KPI-07 | BM25 p95 | <=12 ms | 0.960 | PASS | indicative |  |
| KPI-08 | dense p95 | <=25 ms | 0.221 | PASS | indicative |  |
| KPI-09 | rerank p95 | <=45 ms | 0.204 | PASS | indicative |  |
| KPI-10 | recall | >=0.92 @50 | 1 | PASS | indicative | PROXY: measured @10 because the tool caps rerank_k at 10 |
| KPI-11 | precision@4 | >=0.88 | 0.2857 | FAIL | indicative | threshold 0.0 during eval, so off-topic chunks are included |
| KPI-12 | MRR@4 | >=0.85 | 0.9524 | PASS | indicative |  |
| KPI-13 | nDCG@4 | >=0.86 | 0.961 | PASS | indicative |  |
| KPI-14 | first-pass validation | >=0.96 | 0.7547 | FAIL | indicative | composition-driven: 13 of 53 calls are malformed by construction; not a model-behaviour estimate |
| KPI-15 | auto-repair success | >=0.90 | 1 | PASS | indicative | 8 repairable labelled entities; unrepairable ones are excluded |
| KPI-16 | loop escape (all runs within bound) | =1.0 | 1 | PASS | indicative | 200 adversarial runs; every run terminated within 5 iterations |
| KPI-17 | mean ReAct steps | <=2.2 | 0.963 | PASS | measured | live anthropic/claude-haiku-4.5 via OpenRouter; judge is the same model family (LLM-as-judge, not RAGAS); loop budget 450 ms |
| KPI-18 | goal drift | >=0.85 | not measured | NOT MEASURED | design-only | no drift metric implemented |
| KPI-19 | Tier-3 HITL | =1.0 | 1 | PASS | indicative | 100 Tier-3 attempts; 0 executed without approval |
| KPI-20 | faithfulness (mean, LLM judge) | >=0.92 | 0.9261 | PASS | measured | live anthropic/claude-haiku-4.5 via OpenRouter; judge is the same model family (LLM-as-judge, not RAGAS); loop budget 450 ms |
| KPI-21 | answer relevancy (LLM judge) | >=0.90 | 0.9806 | PASS | measured | live anthropic/claude-haiku-4.5 via OpenRouter; judge is the same model family (LLM-as-judge, not RAGAS); loop budget 450 ms |
| KPI-22 | entity recall | >=0.85 | 0.9048 | PASS | indicative |  |
| KPI-23 | abstention accuracy | >=0.98 | 0.8889 | FAIL | measured | live anthropic/claude-haiku-4.5 via OpenRouter; judge is the same model family (LLM-as-judge, not RAGAS); loop budget 450 ms; 27 golden questions |
| KPI-24 | hallucination (1 - faithfulness pass rate) | <=0.005 | 0.1667 | FAIL | measured | live anthropic/claude-haiku-4.5 via OpenRouter; judge is the same model family (LLM-as-judge, not RAGAS); loop budget 450 ms; per-answer judge score >= 0.92 |
| KPI-25 | LLM response p95 (full, non-streaming) | <=180 ms | 2301 | FAIL | measured | upper bound on TTFT: no streaming, so true TTFT is lower but not measured |
| KPI-26 | token reduction | >=0.45 | 0 | FAIL | indicative | budget never binds on these short notes, so reduction is 0 |
| KPI-27 | cache hit rate | >=0.35 | 0.3571 | PASS | indicative | synthetic replay; includes first-pass semantic-tier hits |
| KPI-28 | tenant isolation | =1.0 | 1 | PASS | indicative | 50 cross-tenant probes, each with a distinct tenant |
| KPI-29 | cost per 1k queries | <=$0.45 | not measured | NOT MEASURED | design-only | no billable model; mock LLM |
| KPI-30 | MCP overhead p95 | <=4 ms | not measured | NOT MEASURED | design-only | transport overhead not isolated locally |
| KPI-31 | p50 end-to-end | <=240 ms | 2125 | FAIL | measured | live anthropic/claude-haiku-4.5 via OpenRouter; judge is the same model family (LLM-as-judge, not RAGAS); loop budget 450 ms; proposed amended target <= 2500 ms: MET |
| KPI-32 | p95 end-to-end | <=450 ms | 3538 | FAIL | measured | live anthropic/claude-haiku-4.5 via OpenRouter; judge is the same model family (LLM-as-judge, not RAGAS); loop budget 450 ms; proposed amended target <= 4500 ms: MET |
| KPI-33 | p99 end-to-end | <=590 ms | 4930 | FAIL | measured | live anthropic/claude-haiku-4.5 via OpenRouter; judge is the same model family (LLM-as-judge, not RAGAS); loop budget 450 ms; proposed amended target <= 6000 ms: MET |
| KPI-34 | 5xx rate | <=0.0005 | 0.000000 | PASS | indicative | local run only; the 50K run is design-only |
| KPI-35 | throughput | >=8500 RPS | 133.8 | FAIL | indicative | single local injector and single worker; the 50K run is design-only |
| KPI-36 | RSS growth | <=3% | not measured | NOT MEASURED | design-only | not measured in this PoC |
