"""Streamlit developer workbench: six tabs, live data from the FastAPI gateway.

Run: uv run streamlit run app.py   (API must be up: uv run uvicorn api.main:app --port 8000)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
import streamlit as st

from config import load_env

load_env()
API = os.environ.get("API_URL", "http://127.0.0.1:8000")
ROOT = Path(__file__).resolve().parent
TIMEOUT = 10.0

st.set_page_config(page_title="The Unreliable Assistant – workbench", layout="wide")
st.session_state.setdefault("tenant", "tenant_7")
st.session_state.setdefault("user", "user_42")
st.session_state.setdefault("session", "sess_ab12cd34ef")
st.session_state.setdefault("approvals", [])


def _post(path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    r = httpx.post(f"{API}{path}", json=body, timeout=TIMEOUT)
    return r.status_code, r.json()


def _get(path: str, params: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    r = httpx.get(f"{API}{path}", params=params, timeout=TIMEOUT)
    return r.status_code, r.json()


def _identity() -> dict[str, str]:
    return {
        "tenant_id": st.session_state.tenant,
        "user_id": st.session_state.user,
        "session_id": st.session_state.session,
    }


with st.sidebar:
    st.header("Identity")
    st.session_state.tenant = st.text_input("tenant_id", st.session_state.tenant)
    st.session_state.user = st.text_input("user_id", st.session_state.user)
    st.session_state.session = st.text_input("session_id", st.session_state.session)
    try:
        ok = httpx.get(f"{API}/health", timeout=2).json().get("status") == "ok"
    except httpx.HTTPError:
        ok = False
    st.caption(f"API {API}: {'UP' if ok else 'DOWN'}")

tabs = st.tabs(["1 Query", "2 Retrieval", "3 Graph", "4 Approvals (HITL)", "5 Audit", "6 KPI scorecard"])

with tabs[0]:
    st.subheader("Ask the assistant")
    tier = st.selectbox("tier", ["TIER_1_READ", "TIER_3_MUTATE"], index=0)
    question = st.text_input("question", "What is the safety protocol for LOT-88392?")
    if st.button("Send", key="q_send"):
        try:
            code, out = _post("/query", {**_identity(), "question": question, "tier": tier})
        except httpx.HTTPError as exc:
            code, out = 0, {"error": str(exc)}
        st.write(f"HTTP {code}")
        st.json(out)
        for step in out.get("trace", []):
            if step.get("approval_id"):
                st.session_state.approvals.append(
                    {
                        "approval_id": step["approval_id"],
                        "tenant_id": st.session_state.tenant,
                        "requested_by": st.session_state.user,
                        "tool": step["tool"],
                    }
                )

with tabs[1]:
    st.subheader("Hybrid retrieval (BM25 ∥ dense → RRF → rerank)")
    q = st.text_input("retrieval query", "LOT-88392 safety protocol", key="ret_q")
    if st.button("Retrieve", key="ret_go"):
        code, out = _post("/query", {**_identity(), "question": q})
        st.write(
            f"status: {out.get('status')}  cache_hit: {out.get('cache_hit')}  faithfulness: {out.get('faithfulness')}"
        )
        st.dataframe(pd.DataFrame([{"citation": c} for c in out.get("citations", [])]))
        st.caption("Retrieval runs through the API with the design default threshold (0.42).")

with tabs[2]:
    st.subheader("Graph (wikilink vault)")
    st.caption("Resolve an anchor and inspect the bounded subgraph (depth ≤ 2 server-side).")
    entity = st.text_input("entity (SKU/LOT)", "SKU-099")
    st.info("Graph endpoints are exercised through the harness in tests; this tab shows the vault index.")
    notes = sorted(p.stem for p in (ROOT / "knowledge_vault").glob("*.md"))
    st.dataframe(pd.DataFrame({"note_id": notes}))

with tabs[3]:
    st.subheader("Pending approvals (Tier 3, requester ≠ approver)")
    if not st.session_state.approvals:
        st.info("No approvals yet. Ask 'Release LOT-00044 from hold' with tier TIER_3_MUTATE on tab 1.")
    st.dataframe(pd.DataFrame(st.session_state.approvals))
    approver = st.text_input("approver user_id", "user_77")
    reason = st.text_input("reason", "reviewed")
    for item in list(st.session_state.approvals):
        cols = st.columns([3, 1, 1])
        cols[0].write(item["approval_id"])
        if cols[1].button("Approve", key=f"a_{item['approval_id']}"):
            code, out = _post(
                "/approvals/decide",
                {
                    "tenant_id": st.session_state.tenant,
                    "user_id": approver,
                    "session_id": st.session_state.session,
                    "approval_id": item["approval_id"],
                    "decision": "APPROVE",
                    "reason": reason,
                },
            )
            cols[2].write(f"HTTP {code}: {out.get('status', out.get('detail'))}")
            st.session_state.approvals = [
                a for a in st.session_state.approvals if a["approval_id"] != item["approval_id"]
            ]
        if cols[2].button("Reject", key=f"r_{item['approval_id']}"):
            code, out = _post(
                "/approvals/decide",
                {
                    "tenant_id": st.session_state.tenant,
                    "user_id": approver,
                    "session_id": st.session_state.session,
                    "approval_id": item["approval_id"],
                    "decision": "REJECT",
                    "reason": reason,
                },
            )
            st.write(f"HTTP {code}: {out.get('status', out.get('detail'))}")

with tabs[4]:
    st.subheader("Audit chain verification (per tenant, SHA-256)")
    to_seq = st.number_input("to_seq", min_value=1, value=1000)
    if st.button("Verify chain", key="audit_go"):
        code, out = _get("/audit/verify", {"tenant_id": st.session_state.tenant, "from_seq": 1, "to_seq": int(to_seq)})
        st.write(f"HTTP {code}")
        st.json(out)

with tabs[5]:
    st.subheader("36-KPI scorecard (evidence classes shown)")
    md = ROOT / "results" / "scorecard.md"
    if md.exists():
        st.markdown(md.read_text(encoding="utf-8"))
        rows = json.loads((ROOT / "results" / "scorecard.json").read_text(encoding="utf-8"))
        st.dataframe(pd.DataFrame(rows))
    else:
        st.warning("Run: uv run python -m tests.eval.scorecard")
