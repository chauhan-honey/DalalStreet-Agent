"""
USER INTERFACE  ──  frontend/app.py
===================================

WHAT THIS FILE IS
    The dashboard the user actually interacts with. It collects a company + a
    research question, sends them to the backend, and shows the multi-agent
    workflow's progress and final report live as it happens. Run it with:
        streamlit run frontend/app.py --server.port 8501

WHAT IS STREAMLIT
    A Python library for building simple web UIs with no HTML/JS. You write
    top-to-bottom Python (st.title, st.button, st.dataframe, ...) and Streamlit
    renders it as a web page. The script re-runs from the top on each interaction
    (e.g. when a button is clicked).

HOW IT TALKS TO THE BACKEND (the flow)
    THIS FILE ── POST /api/v1/research/stream (stream=True) ──► backend endpoints.py
       ▲                                                              │
       └────────── SSE "data:" frames (one per graph node) ──────────┘
    We read those frames one line at a time and update the UI for each node:
    planner ▸ rag_worker ▸ market_worker ▸ critic ▸ synthesizer. The synthesizer
    frame carries the final markdown report.

    The backend address is read from BACKEND_URL, and the shared auth secret
    from BACKEND_API_KEY (see _get_config below for where these come from).
    The backend requires that key as an X-API-Key header on /research/stream
    — deployed on the public internet with no other access control, without
    it anyone with the URL could trigger real Gemini API calls on our quota.
"""
import json
import os

import requests   # HTTP client used to call the backend and read the SSE stream
import streamlit as st

# Configure the browser tab and page layout. Must be the first Streamlit call.
st.set_page_config(page_title="DalalStreet Agent", layout="wide", page_icon="📈")


def _get_config(key: str, default: str = "") -> str:
    """Read a config value from Streamlit secrets first, then env vars.

    Streamlit Community Cloud has no plain "environment variables" panel —
    config there is set via its Secrets manager, read through st.secrets.
    Locally (plain `streamlit run`), there's usually no secrets.toml at all,
    and st.secrets raises in that case, so we fall back to os.getenv (which
    reads from a real env var, or your local .env if something already
    loaded it) — one function that works in both places unmodified.
    """
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return os.getenv(key, default)


# Where the backend lives. Defaults to localhost; override with BACKEND_URL to
# point at a deployed API. STREAM_ENDPOINT is the specific route we POST to.
BACKEND_URL = _get_config("BACKEND_URL", "http://localhost:8000")
BACKEND_API_KEY = _get_config("BACKEND_API_KEY", "")
STREAM_ENDPOINT = f"{BACKEND_URL}/api/v1/research/stream"

# Page header.
st.title("📈 DalalStreet-Agent: Indian Financial Multi-Agent Auditor")
st.markdown(
    "Autonomous research system cross-examining Annual Report disclosures "
    "against live NSE/BSE fundamentals."
)

# Split the page into a narrow input column (left) and a wide output column (right).
col_left, col_right = st.columns([1, 2])

with col_left:
    # Company picker. We map the friendly name to its yfinance ticker symbol,
    # because the backend/tools need the ticker (e.g. "TCS.NS").
    company_choice = st.selectbox(
        "Select Target Company",
        ["Tata Consultancy Services", "Reliance Industries", "Infosys"],
    )
    ticker_map = {
        "Tata Consultancy Services": "TCS.NS",
        "Reliance Industries": "RELIANCE.NS",
        "Infosys": "INFY.NS",
    }
    ticker = ticker_map[company_choice]

    # Free-text box for the research directive, pre-filled with an example.
    query = st.text_area(
        "Audit / Research Directive",
        value=(
            "Evaluate management disclosures regarding cloud margin stability and "
            "cross-examine with operating margins and cash flows."
        ),
    )
    # The button that kicks off a run. `run_button` is True only on the rerun
    # triggered by the click.
    run_button = st.button(
        "Start Multi-Agent Audit", type="primary", use_container_width=True
    )

with col_right:
    # Live status area + empty placeholders we fill in as frames arrive. Creating
    # the placeholders up front lets us update them in place during the stream.
    status_box = st.status("Workflow Execution", expanded=True)
    market_placeholder = st.empty()     # will hold the fundamentals table
    findings_placeholder = st.empty()   # will hold the critic's findings table
    report_placeholder = st.empty()     # will hold the final markdown report


def _render_market(data: dict) -> None:
    """Render the market_worker payload as a simple two-column metrics table.

    `data` is the node output from a market_worker SSE frame. We flatten the
    nested {"quote": {...}, "ratios": {...}} into flat metric/value rows.
    """
    market = data.get("market_data", {})
    if market:
        # The MCP tool result can arrive as a plain dict, a JSON string, or a
        # list of content parts (depending on the MCP adapter version), so coerce
        # each side to a flat dict before merging.
        rows = {**_coerce_to_dict(market.get("quote")), **_coerce_to_dict(market.get("ratios"))}
        market_placeholder.dataframe(
            {"metric": list(rows.keys()), "value": [str(v) for v in rows.values()]},
            use_container_width=True,
        )


def _coerce_to_dict(value) -> dict:
    """Normalise an MCP tool payload (dict / JSON string / content-part list) to a flat dict."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        # A JSON string like '{"symbol": "TCS.NS", ...}'.
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {"value": value}
        except json.JSONDecodeError:
            return {"value": value}
    if isinstance(value, list):
        # e.g. [{"type": "text", "text": "{...json...}"}] or a list of dict parts.
        merged: dict = {}
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                merged.update(_coerce_to_dict(item["text"]))
            elif isinstance(item, dict):
                merged.update(item)
        return merged
    return {}


def _render_findings(data: dict) -> None:
    """Render the critic's per-claim audit findings as a table, if any were produced."""
    findings = data.get("audit_findings", [])
    if findings:
        findings_placeholder.dataframe(findings, use_container_width=True)


# Everything below runs only when the user clicks the button.
if run_button:
    status_box.write("⚙️ Initializing StateGraph...")
    # The JSON body the backend's ResearchRequest model expects.
    payload = {"company_name": company_choice, "ticker": ticker, "user_query": query}

    headers = {"X-API-Key": BACKEND_API_KEY} if BACKEND_API_KEY else {}
    try:
        # stream=True keeps the HTTP connection open so we can read SSE frames as
        # they arrive rather than waiting for the whole response.
        with requests.post(STREAM_ENDPOINT, json=payload, headers=headers, stream=True, timeout=180) as resp:
            if resp.status_code == 401:
                # Distinguish "wrong/missing key" from a generic connection
                # failure so a misconfigured secret is obvious, not a mystery.
                status_box.update(label="Authentication Error", state="error")
                st.error(
                    "Backend rejected the request (401 Unauthorized) — BACKEND_API_KEY "
                    "in this app's secrets doesn't match the backend's configured key."
                )
                st.stop()
            # Raise if the backend returned any other HTTP error status (4xx/5xx).
            resp.raise_for_status()
            # Iterate the response line by line as the server pushes frames.
            for line in resp.iter_lines():
                if not line:
                    continue  # SSE frames are separated by blank lines; skip them
                decoded = line.decode("utf-8")
                # We only care about the "data: ..." payload lines.
                if not decoded.startswith("data: "):
                    continue
                try:
                    # Strip the "data: " prefix and parse the JSON payload.
                    event = json.loads(decoded[len("data: "):])
                except json.JSONDecodeError:
                    continue  # ignore any malformed frame instead of crashing

                node = event.get("node")   # which graph node produced this frame
                data = event.get("data", {})

                # Update the UI based on which node just finished. This mirrors the
                # execution order of graph.py so the user follows along step by step.
                if node == "planner":
                    status_box.write("📋 **Planner:** Decomposition & sub-queries generated.")
                elif node == "rag_worker":
                    status_box.write("📄 **RAG Worker:** Extracted PDF disclosures with page citations.")
                elif node == "market_worker":
                    status_box.write("📈 **Market Worker (MCP):** Retrieved live NSE/BSE fundamentals.")
                    _render_market(data)
                elif node == "critic":
                    verdict = data.get("critic_verdict")
                    status_box.write(f"🔍 **Critic Node:** Evaluation complete. Verdict: `{verdict}`")
                    _render_findings(data)
                elif node == "synthesizer":
                    # Final node: mark the workflow complete and show the report.
                    status_box.update(label="Audit Complete", state="complete", expanded=False)
                    report_placeholder.markdown(data.get("final_report", "_No report produced._"))
                    # Terminal node: stop reading immediately instead of blocking on
                    # iter_lines() until the server closes the connection. Without
                    # this, Streamlit would not repaint the report until the stream
                    # times out (the run appears stuck on a spinner).
                    break
                elif node == "error":
                    # The backend sent a sanitised error frame (a node failed).
                    status_box.update(label="Execution Error", state="error")
                    st.error(f"Backend error: {data.get('message', 'unknown error')}")
                    break
    except requests.exceptions.RequestException as exc:
        # The backend was unreachable or timed out. Show a friendly message rather
        # than letting the Streamlit app crash with a traceback.
        status_box.update(label="Connection Error", state="error")
        st.error(f"Could not reach backend at {STREAM_ENDPOINT}: {exc}")
