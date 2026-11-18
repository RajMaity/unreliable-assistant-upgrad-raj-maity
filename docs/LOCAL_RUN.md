# Running the local PoC

Prerequisites: Python 3.11, Graphviz (for the `dot` render step).

Option B, plain pip (`requirements.txt` pins every resolved package, dev tools included):

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

With Option B, drop the `uv run` prefix from every command below.

Option A, with `uv` (uses `uv.lock`):

```bash
uv python install 3.11
uv sync                                                        # project venv: .venv
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict contracts mcp_servers harness plugins ingress api
uv run pytest tests/unit tests/integration tests/chaos tests/redteam -q
uv run pytest tests/eval -q                                    # writes results/*.json
uv run python -m tests.eval.scorecard                          # results/scorecard.{md,json}
```

Full stack (three terminals, or background):

```bash
STATE_DB=results/state.sqlite3 uv run python -m mcp_servers.state_store --port 8081
uv run uvicorn api.main:app --host 127.0.0.1 --port 8000 --workers 1
uv run streamlit run app.py                                    # workbench on :8501
```

Smoke test:

```bash
curl -s localhost:8000/query -H 'Content-Type: application/json' \
  -d '{"tenant_id":"tenant_7","user_id":"user_42","session_id":"sess_ab12cd34ef",
       "question":"What is the safety protocol for LOT-88392?","tier":"TIER_1_READ"}'
```

Local load (indicative): `uv run python -m load_tests.local_load --concurrency 20 --requests 400`.
Scaled runs (design-only): `k6 run -e VUS=... load_tests/k6_50k_load.js` and
`jmeter -n -t load_tests/jmeter_50k_plan.jmx -l results/local.jtl -JTHREADS=... `. Neither tool is installed here.

Backend selection as implemented:
- `STATE_BACKEND`: `sqlite` only (any other value exits with an error).
- `CACHE_BACKEND`: `fake` (default, fakeredis) or `redis` (uses `REDIS_URL`).
- `OTEL_EXPORTER`: `console` (default) only. `otlp` fails with `MANIFEST_INVALID`.
- `EMBED_BACKEND`, `RERANK_BACKEND`, `LLM_BACKEND`: not read. The mock implementations are hard-wired.

## LLM (OpenRouter) via a `.env` file

1. Copy the template and add your key (the file is git-ignored):

   ```bash
   cp .env.example .env          # then edit .env and set OPENROUTER_API_KEY=sk-or-...
   ```

2. Start the API as usual. It reads `.env` from the project root automatically. Variables already set in
   your shell take precedence over the file. To use a file elsewhere, set `ENV_FILE=/path/to/file`.

`LLM_BACKEND=mock` runs fully offline with no key. The test suite always runs offline.

Live evaluation (calls the API and costs a few cents) is opt-in:

```bash
RUN_LIVE_EVAL=1 python -m pytest tests/eval -m live -q
```

Budgets: tool steps have a 450 ms budget (`LOOP_BUDGET_MS`). LLM time has its own budget
(`LLM_BUDGET_MS`, 3000 ms, proposed in `docs/ADR_AMENDMENT_001.md`). Both are unchanged by default.
