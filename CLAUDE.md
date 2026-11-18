# CLAUDE.md — The Unreliable Assistant (enterprise rebuild, local PoC)

Governs every implementation session in this repository. Read this file and `design.md` before any change.

## 1. Source of truth
- `design.md` (repo root) is the **locked architectural contract**. It overrides this file, the specs in `Docs/`, and any prior assumption.
- Do not deviate from anything it defines: JSON-RPC/MCP tool schemas, Pydantic v2 models, regex repair rules, RBAC tiers, error codes, latency budgets (p50 ≤ 240 / p95 ≤ 450 / p99 ≤ 590 ms; per-stage budgets in §1.4), KPI thresholds (§1.5), and folder paths.
- If implementation requires a change to the contract, **stop**. Propose the change as a new or amended ADR in `design.md` and get approval before writing code that depends on it.
- Spec conflicts are already resolved in `design.md` §1.7 (e.g., HNSW not IVF-PQ, triage ≤ 25 ms, 45 ms FP16 rerank binding). Do not reopen them in code.

## 2. Hard gates (blocking)
- **Gate 1:** `design.md` must exist at repo root with all seven sections (1.1–1.7). Before writing any application logic, verify it is present and unchanged from its locked version.
- **Gate 2:** `docs/workflow_orchestration.png` must exist, rendered at **300 DPI**. Regenerate from `docs/workflow_orchestration.dot` with:
  `dot -Tpng -Gdpi=300 docs/workflow_orchestration.dot -o docs/workflow_orchestration.png`
  Check the PNG exists before starting Phase 1.
- If either gate fails, the only permitted work is fixing the gate artifact. No `src/`, `mcp_servers/`, `api/`, or test code until both pass.

## 3. Folder layout (from `design.md`; do not invent alternatives)
| Path | Purpose |
|---|---|
| `design.md`, `CLAUDE.md`, `mcp_config.json` | contract, governance, MCP + plugin registration |
| `docs/` | `workflow_orchestration.{dot,png}` (Gate 2) |
| `contracts/common.py` | shared Pydantic v2 types (`CallContext`, `SKU`, `LOT`, `Tier`, …) |
| `mcp_servers/rag_server.py` | `mcp-hybrid-rag` (stdio) |
| `mcp_servers/graphify_server.py` | `mcp-graphify-vault` (stdio) |
| `knowledge_vault/` | atomic Obsidian notes with YAML frontmatter and `[[wikilinks]]` |
| `load_tests/k6_50k_load.js`, `load_tests/jmeter_50k_plan.jmx` | load-test scripts |
| `app.py` | Streamlit developer workbench (6 tabs) |
| `Docs/` | source specs. Read-only. Never edit. |

Paths not yet defined in `design.md` (`mcp-state-store` server, FastAPI entry module, RAGAS runner, test layout) must be added to `design.md` first. Until then, use the proposed names in §5, mark them as proposed, and do not treat them as locked.

## 4. Tech stack and standards
- **Python 3.11** only. Manage with `uv`. Dependencies pinned in `pyproject.toml` + `uv.lock`.
- **FastAPI + Uvicorn** (async). No blocking I/O on the request path. No `requests`; use `httpx` (async).
- **MCP** JSON-RPC 2.0: stdio for `hybrid-rag` / `graphify-vault`; streamable-HTTP for `state-store` (port 8081). Every call carries `_meta` (`traceparent`, `tenant_id`, `request_id`).
- **Pydantic v2** everywhere, with `extra="forbid"`, `strict=True`, `frozen=True` (see `contracts/common.py`). Never accept untyped dicts across a boundary.
- **Pluggy** for lifecycle hooks: `pre_execution`, `tool_dispatch`, `post_execution`, `span`. Plugins are registered from `mcp_config.json`. HITL is a core gate, not a plugin.
- **Tenant isolation:** every SQL query and Redis key includes `tenant_id`. Cache keys are `t:{tenant_id}:` + `SHA256(tenant_id:user_id:norm_query)`. Never write a query or key without it.
- **Security:** no raw PII in logs, cache, DB, or traces. Redact before any write. Fail closed on PII or injection plugin errors.
- **Audit:** SHA-256 hash chain (`prev_hash || canonical_json(event)`). Append-only.
- **Local PoC backends** are selected by env var (design §1.3): `STATE_BACKEND`, `CACHE_BACKEND`, `EMBED_BACKEND`, `RERANK_BACKEND`, `LLM_BACKEND`, `OTEL_EXPORTER`. Mock outputs must be tagged `indicative` in KPI reports.
- **Lint/format/type:** `ruff check`, `ruff format`, `mypy --strict` on `contracts/` and `mcp_servers/`. Zero warnings to merge.
- **Style:** no speculative abstractions. Implement exactly what the current phase's contract requires.

## 5. Commands
Commands below are targets. Phase 0 creates the files they need. Run them only after the referenced file exists.

**Environment**
```bash
uv python install 3.11
uv venv --python 3.11 && source .venv/bin/activate
uv sync                    # runtime + dev deps
```
Graphviz is a system dependency (`sudo apt-get install graphviz`).

**MCP servers** (stdio servers are launched by the host client, but can be run for debugging)
```bash
uv run mcp_servers/rag_server.py
uv run mcp_servers/graphify_server.py
uv run python -m mcp_servers.state_store --port 8081    # path proposed, not yet in design.md
```

**FastAPI**
```bash
uv run uvicorn api.main:app --host 127.0.0.1 --port 8000 --workers 1    # api/main.py proposed
```

**Graph build (Gate 17)**
```bash
uv run graphify build ./src ./knowledge_vault --output graph.json
```

**Tests**
```bash
uv run pytest tests/unit -q                       # fast, no network, no model weights
uv run pytest tests/integration -q                # spins up MCP servers + SQLite/fakeredis
uv run pytest tests/eval -m ragas -q             # RAGAS golden-set run (indicative with mock judge)
uv run pytest tests/redteam -q                    # 100-prompt red-team suite (Gate 14)
uv run pytest tests/chaos -q                      # loop-escape + breaker suite (KPI-16)
uv run pytest --cov=contracts --cov=mcp_servers --cov-fail-under=90
```

**Load (design-only at 50K; local = scaled proxy)**
```bash
k6 run load_tests/k6_50k_load.js                                  # local: override VUs via -e VUS=... for scaled run
jmeter -n -t load_tests/jmeter_50k_plan.jmx -l results/local.jtl  # local single-injector run
```

**Streamlit workbench**
```bash
uv run streamlit run app.py
```

**Quality gate (run before declaring a phase done)**
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy --strict contracts mcp_servers
uv run pytest tests/unit tests/integration -q
```

## 6. Phase workflow (strict)
Phases follow the build order in the reconciliation plan. Implement **one phase at a time**. Do not start the next phase until the current phase's exit criteria are met and reported.

| Phase | Scope | Exit criteria |
|---|---|---|
| **P0** Scaffold | `pyproject.toml`, `uv.lock`, `contracts/common.py`, `mcp_config.json`, test skeleton | Gate 1 + 2 verified; `uv sync` clean; quality gate passes on empty tests |
| **P1** Contracts | Pydantic models for all MCP tools in `design.md` §1.2; error codes; regex repair rules R1–R4 | Unit tests cover every schema, repair rule, and error code; mypy strict clean |
| **P2** MCP servers | `rag_server.py` (BM25 → dense → RRF → CE), `graphify_server.py`, state-store | Per-stage latency recorded; integration tests over stdio/HTTP; stage budgets from §1.4 checked |
| **P3** Harness & loop | `ToolExecutionHarness`, ReAct state machine, breaker, HITL gate, subagents | Chaos suite: 100% loop escape (KPI-16); Tier-3 always `PENDING_APPROVAL` (KPI-19) |
| **P4** Ingress & plugins | pluggy hookspecs, 5 plugins, triage, cache, abstention | Ingress/triage KPIs 01–06 measured; cross-tenant probe = 0 leaks (KPI-28) |
| **P5** Evaluation & obs | RAGAS runner, red team, OTel spans, KPI scorecard | 36-KPI scorecard generated with evidence class labels (`measured`/`indicative`/`design-only`) |
| **P6** Load & UI | k6/JMeter scaled runs, `app.py` six tabs | Scaled-load report; Streamlit tabs render live data |

**Per-phase rules**
1. State the phase and its exit criteria at the start of the session.
2. Write the **tests first** for the phase's contract, then the implementation.
3. Every module ships with unit tests. Every MCP server and plugin ships with at least one integration test.
4. Do not modify a previous phase's public contract. If required, stop and raise an ADR.
5. At phase end, report each exit criterion as PASS / FAIL with command output. Do not claim a pass without running the command.
6. Commits use the attribution line from the session reminder; never commit without a request.

## 7. Prohibited
- Editing `Docs/` or the locked sections of `design.md` silently.
- Global (non-tenant-scoped) Redis keys, or cross-tenant cache reads.
- Unbounded loops or recursion without state mutation; more than 5 ReAct iterations.
- Autonomous execution of Tier-3 mutations (must pause at `PENDING_APPROVAL`).
- Logging or persisting raw PII.
- Presenting `indicative` or `design-only` KPI results as `measured`.
- Adding dependencies outside `pyproject.toml`, or using PyTorch in the inference path (ONNX Runtime only).
