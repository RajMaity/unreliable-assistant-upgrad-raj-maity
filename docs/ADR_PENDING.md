# Pending ADR items (need approval before they are folded into design.md)

`design.md` is unchanged (md5 `aeb9cb60bdc6c943d1d488a6491acc01`). The items below are deviations from, or
additions to, the locked contract that the implementation relies on. CLAUDE.md §1 and §3 require an approved
ADR before code depends on them.

## Deviations from locked text
1. **UUID wire fields are lax.** `Strict` keeps `strict=True`, but `CallContext.idempotency_key` and
   `HitlDecideIn.approval_id` use `Field(strict=False)`. Strict Python-mode validation rejects the JSON string
   form of a UUID, so every tool call failed at FastMCP argument validation. Alternative: parse every argument
   with `model_validate_json`. This needs a decision.
2. **`mcp` pinned `>=1.10,<2`.** The `mcp` 2.x release renamed FastMCP to `MCPServer`. The v1 API is used.
3. **Tool arguments are wrapped as `{"payload": {...}}`.** FastMCP v1 builds the argument schema from function
   parameters, so the model sits under `payload`. The design says `arguments` is the model directly.
4. **`ResolveAnchorIn.entities: list[SKU | LOT | str]`** accepts any string, so the SKU/LOT repair (R1/R2) never
   runs on entities. Repair only runs on `workflow_transition.entity`. Suggest typing entities as
   `list[SKU | LOT]`, or documenting that free text is allowed.
5. **`SubgraphOut.raw_tokens_est` versus compression.** The budget does not bind on the current notes, so token
   reduction is 0% (KPI-26 fails). This is a finding about the design target, not a code bug.
6. **Ruff scope.** Ruff excludes `*.md` and `docs/` from formatting, because ruff 0.17 formats Python code
   blocks inside markdown, which would rewrite the locked spec.

## Proposed items (not in design.md)
- Packages: `harness/` (executor, loop, registry, transport), `plugins/` (hookspecs, guards, breaker, ragas,
  otel, loader), `ingress/` (triage, pipeline), `api/main.py`, `app.py`, `tests/eval/`, `load_tests/`.
- Module path: `mcp_servers/state_store.py` (run as `python -m mcp_servers.state_store --port 8081`).
- Models without a design schema: `ChunkResource`, `RagHealthOut`, `GraphStatsOut`, `FormSubmitIn`,
  `HitlDecideOut`, `PendingOut`, `AuditEvent`. Their fields follow the design's tables.
- `mcp_config.json` schema (`schema_version: 0.1-proposed`).
- Subagent `APPROVER` (`hitl_decide` only). Design lists "approver" without a subagent.
- `audit_verify` takes its tenant from `_meta`, because its input has no `CallContext` (design lists only
  `from_seq`/`to_seq`).
- `STATE_BACKEND=postgres` and `OTEL_EXPORTER=otlp` are rejected with `MANIFEST_INVALID` (not implemented).
- `CACHE_BACKEND=redis` uses the `redis` client (`REDIS_URL`). `fake` (fakeredis) is the default.
- Semantic cache tier: the cached value carries `semantic_vec`; cosine ≥ 0.92 is checked over the tenant prefix.
- Injection shield normalises (NFKC, zero-width strip) before matching, the same canonicalisation as R3.

## Known gaps (not ADR items, reported in the scorecard)
- Graph build (`uv run graphify build ...`, Gate 17): the `graphify` CLI is not a dependency here. The graph is
  built in-process from wikilinks.
- `docs/workflow_orchestration.png` was re-rendered with `dot -Tpng -Gdpi=300`. Graphviz writes no `pHYs` DPI
  chunk, so the 300 DPI is evidenced by the render command, not the file metadata.

## LLM integration (added with OpenRouter support)
- **Provider and model.** OpenRouter (OpenAI-compatible API) with `anthropic/claude-haiku-4.5` by default. The
  account's guardrail blocks several models (for example `openai/gpt-4o-mini`, `google/gemini-2.5-flash`).
  Switch with `OPENROUTER_MODEL`.
- **Where the LLM runs.** Planner (ReAct REASONING) and grounded answer generation in `harness/llm_policy.py`.
  The RAGAS-style judge is in `llm/judge.py`, used only by the evaluation suite.
- **Planner and generation share one call.** The design lists `llm.generate` as a separate step. Merging them
  saves one round trip, which matters for the latency budget.
- **Loop budget conflict (needs ADR).** The design's ADR-004 budget is 450 ms total. One remote planner call takes
  about 1.5–2 s, so the loop escapes after the first LLM decision (verified against the live API). The live
  evaluation used `LOOP_BUDGET_MS=20000`, a measurement override and not the contract value. Options: (a) raise
  the budget for the `api` backend, (b) a local model within budget, (c) redefine the latency KPI for remote LLMs.
- **Triage stays deterministic.** The design describes the triage agent as `temp=0.0`, but its budget is 25 ms.
  A remote call cannot meet that, so triage is rule-based.
- **Judge independence.** The judge is the same model family as the generator, so its scores are less independent
  than a separate judge model would give. Recommend a second model for judging.
- **Not implemented.** The `DIRECT_ANSWER` route, the on-path NLI claim check (Verification subagent), and
  streaming (so TTFT is not measured, only the full response time).
- **Key handling.** The key is read from `OPENROUTER_API_KEY`, from the environment or a git-ignored `.env` file
  loaded by `config.py` (no new dependency). It is never written to the repo or results.
  Keys pasted into chat transcripts should be rotated.
