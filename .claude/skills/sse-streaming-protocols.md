# Skill: SSE Streaming Protocols

> Implementing Server-Sent Events with FastAPI `StreamingResponse` to trace LangGraph execution node-by-node in real time.

---

## 1. Why SSE (not WebSockets)

The run is **one-directional server → client** progress: the client submits a research request, then watches nodes fire. SSE over plain HTTP is the right fit — no bidirectional channel, trivial to consume from Streamlit/`requests`, and it maps 1:1 onto LangGraph's `astream` event iterator.

---

## 2. The SSE wire format

Each event is a `data:` line terminated by a **blank line** (`\n\n`):

```text
data: {"node": "planner", "data": {"plan": ["..."]}}\n\n
data: {"node": "rag_worker", "data": {"rag_context": [...]}}\n\n
data: {"node": "critic", "data": {"critic_verdict": "PASS", ...}}\n\n
```

Rules:
- Prefix every payload with `data: ` and terminate with `\n\n`.
- One JSON object per frame.
- `media_type="text/event-stream"`.
- Serialise with a `default=str` fallback so non-JSON-native values (datetimes, numpy scalars from yfinance) don't crash the stream.

---

## 3. Streaming a LangGraph run

```python
import asyncio
import json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from backend.app.graph import compiled_graph

router = APIRouter()


class ResearchRequest(BaseModel):
    company_name: str
    ticker: str
    user_query: str


@router.post("/research/stream")
async def stream_financial_audit(req: ResearchRequest):
    async def event_generator():
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
            async for event in compiled_graph.astream(initial_state):
                for node_name, node_output in event.items():
                    payload = {"node": node_name, "data": node_output}
                    yield f"data: {json.dumps(payload, default=str)}\n\n"
                    await asyncio.sleep(0.05)          # flush pacing, non-blocking
        except Exception as exc:                        # never leak a stack trace to the stream
            yield f"data: {json.dumps({'node': 'error', 'data': {'message': str(exc)}})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")
```

Key points:
- `compiled_graph.astream(...)` yields one dict per node step: `{node_name: partial_state}`. Emit one SSE frame per node → the UI traces execution live.
- `await asyncio.sleep(0.05)` yields control to flush the buffer; it is **not** `time.sleep` (which would block the loop).
- Wrap the generator in try/except and emit a structured `error` frame — never let a traceback (which may echo inputs) escape into the stream.

---

## 4. Initial state seeding

The reducer-backed keys (`rag_context`, `market_data`) **must** be seeded to empty (`[]`, `{}`) and `iteration_count` to `0` in the initial state, or the first reduction / `get` has nothing to build on.

---

## 5. Headers & proxies (production hardening)

If placed behind a proxy that buffers responses, disable buffering so frames arrive live:

```python
return StreamingResponse(
    event_generator(),
    media_type="text/event-stream",
    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
)
```

---

## 6. Security on the stream

- **Never** serialise secrets into a frame. `node_output` is graph state — audit it so `GOOGLE_API_KEY` or raw credentials can't ride along.
- The `error` frame carries `str(exc)` only — sanitise if exceptions could contain sensitive input.

---

## 7. Anti-patterns

- Missing the trailing `\n\n` (client never sees a complete event).
- `time.sleep` inside the async generator (blocks the event loop).
- `json.dumps` without `default=str` (crashes on yfinance numpy/datetime values).
- Letting an unhandled exception break the stream with a partial frame.
- Buffering the whole run before responding (defeats the purpose).

---

## 8. Validation

1. `curl -N` the endpoint; confirm frames arrive incrementally, one per node.
2. Confirm each frame is `data: {json}\n\n`.
3. Force a node error; confirm a clean `error` frame, no stack trace.
4. Grep frames for the API key value; must be absent.
