"""
HTTP API ROUTES  ──  backend/app/api/v1/endpoints.py
====================================================

WHAT THIS FILE IS
    The web-facing entry point. It defines the URL routes the frontend calls. The
    most important one starts a research run and STREAMS the graph's progress back
    to the browser live, node by node.

THE FRAMEWORKS
    - FastAPI: a Python web framework. An `APIRouter` groups related routes; each
      `@router.post(...)` / `@router.get(...)` function becomes a URL endpoint.
    - Pydantic (v2): validates incoming JSON. `ResearchRequest` below describes
      the required request body; FastAPI rejects malformed requests automatically.
    - SSE (Server-Sent Events): a simple way for the server to push a stream of
      messages to the browser over one long-lived HTTP response. Each message is
      a line formatted as:  `data: <json>\\n\\n`. The frontend reads these lines
      one at a time and updates the UI as each graph node finishes.

WHERE THIS SITS IN THE PROJECT (the flow)
    Streamlit frontend (frontend/app.py)
        │  POST /api/v1/research/stream  {company_name, ticker, user_query}
        ▼
    THIS FILE  ── builds the initial AgentState and calls compiled_graph.astream()
        │  (compiled_graph is defined in graph.py)
        ▼
    one SSE "data:" frame per node  ──►  browser renders progress + final report

    This router is attached to the app in main.py under the /api/v1 prefix.
"""
import asyncio
import json
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from backend.app.core.config import settings
from backend.app.graph import compiled_graph  # the compiled LangGraph workflow

# A router collects the endpoints defined below; main.py mounts it on the app.
router = APIRouter()


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Guards /research/stream so it isn't a free-for-all on the public internet.

    Deployed with --ingress external (needed so the Streamlit frontend, hosted
    on a completely different platform, can reach it), this endpoint has no
    network-level privacy — anyone with the URL could otherwise trigger real
    Gemini API calls against our free-tier quota. The frontend sends the same
    key back as the X-API-Key header (see frontend/app.py).

    If BACKEND_API_KEY isn't set at all (e.g. local `uvicorn --reload`, never
    exposed publicly), the check is skipped — convenience for local dev.
    Whenever a key IS configured (always true in the deployed container), a
    missing/wrong header is rejected. secrets.compare_digest avoids leaking
    the key's length/contents through response-time differences.
    """
    if not settings.BACKEND_API_KEY:
        return
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.BACKEND_API_KEY):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or missing API key")


class ResearchRequest(BaseModel):
    """Shape + validation rules for the POST body of /research/stream.

    FastAPI parses the incoming JSON into this model and validates it BEFORE our
    code runs. If a field is missing or fails a rule, the caller gets a clear 422
    error automatically — our handler only ever sees valid data.
    """

    company_name: str = Field(..., min_length=1)                 # must be non-empty
    ticker: str = Field(..., pattern=r"^[A-Za-z0-9.\-]+$")       # e.g. "TCS.NS"; letters/digits/./-
    user_query: str = Field(..., min_length=3)                   # the research question


@router.get("/health")
async def health() -> dict:
    """Liveness probe. Hitting GET /api/v1/health returns {"status": "ok"}.

    Used by uptime checks / load balancers to confirm the service is running.
    """
    return {"status": "ok"}


@router.post("/research/stream", dependencies=[Depends(require_api_key)])
async def stream_financial_audit(req: ResearchRequest):
    """Run the full agent workflow and stream each node's output as it completes.

    Returns a StreamingResponse (SSE). The inner `event_generator` is an async
    generator: every time it `yield`s a string, FastAPI flushes that chunk to the
    browser immediately — so the user sees progress in real time instead of
    waiting for the whole run to finish.
    """
    async def event_generator():
        # Build the STARTING shared state for the graph. The reducer-backed keys
        # (rag_context, market_data) must begin empty, and iteration_count at 0,
        # so the nodes and the retry loop have valid initial values.
        initial_state = {
            "company_name": req.company_name,
            "ticker": req.ticker,
            "user_query": req.user_query,
            "rag_context": [],
            "market_data": {},
            "audit_findings": [],
            "iteration_count": 0,
        }
        try:
            # compiled_graph.astream(...) runs the workflow and yields one event
            # each time a node finishes, shaped like {node_name: node_output}.
            async for event in compiled_graph.astream(initial_state):
                for node_name, node_output in event.items():
                    # Wrap the node's result as an SSE frame the frontend parses.
                    payload = {"node": node_name, "data": node_output}
                    # default=str lets json.dumps handle odd values (e.g. numbers
                    # from yfinance) without crashing the stream.
                    yield f"data: {json.dumps(payload, default=str)}\n\n"
                    # Briefly yield control so the chunk is flushed to the client.
                    # asyncio.sleep (not time.sleep) keeps the event loop free.
                    await asyncio.sleep(0.05)
        except Exception as exc:
            # If any node errors, send ONE clean error frame instead of leaking a
            # raw stack trace (which could expose internals) into the stream.
            err = {"node": "error", "data": {"message": str(exc)}}
            yield f"data: {json.dumps(err)}\n\n"

    # Tell the browser this is an event stream. The headers disable caching and
    # proxy buffering so each frame arrives as soon as it is produced.
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
