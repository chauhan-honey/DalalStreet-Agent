# Skill: Clean Architecture in Python

> Layer separation, strict typing, async/await discipline, and Pydantic v2 validation for a maintainable, testable service.

---

## 1. The four layers

```text
┌──────────────────────────────────────────────┐
│  API layer        backend/app/main.py, api/** │  ← HTTP, CORS, SSE framing, request models
├──────────────────────────────────────────────┤
│  Orchestration    backend/app/graph.py        │  ← LangGraph nodes, control flow, LLM calls
├──────────────────────────────────────────────┤
│  Tools            backend/app/mcp_server/**    │  ← FastMCP tools (market data)
├──────────────────────────────────────────────┤
│  Storage          backend/app/rag/**          │  ← ChromaDB + PyMuPDF vector store
└──────────────────────────────────────────────┘
                    core/  (config.py, state.py) = shared contracts
```

### The dependency rule
Dependencies point **inward/downward only**:
- API imports Orchestration (`compiled_graph`); it never imports the vector store or yfinance directly.
- Orchestration imports Storage (`FinancialVectorStore`) and calls Tools **via the MCP client**, not by importing tool internals.
- Tools and Storage never import from API or Orchestration.
- `core/` holds shared contracts (`settings`, `AgentState`) and depends on nothing above it.

> Reach-around = defect. Example: an endpoint that calls `yf.Ticker` directly, or a node that opens ChromaDB's client itself instead of using `FinancialVectorStore`.

---

## 2. Strict typing (Python 3.10+)

- Every function has typed parameters and a typed return.
- Prefer modern syntax: `list[dict[str, Any]]`, `str | None`, `dict[str, Any]`.
- Reserve `Any` for genuinely dynamic payloads (raw yfinance `.info`, LLM JSON); model everything else.
- Use `Literal[...]` for closed sets (`Literal["PASS", "NEEDS_RETRY", "FAIL"]`).

```python
async def rag_worker_node(state: AgentState) -> dict[str, list[dict[str, Any]]]:
    ...
```

---

## 3. Async/await discipline

- The entire request path is `async`: FastAPI handler → `astream` → each node → LLM/tool `ainvoke`.
- **Never** put blocking I/O on the async hot path (`requests.get`, blocking file reads, sync DB calls). If a library is sync-only and slow, offload with `await asyncio.to_thread(fn, ...)`.
- Use `async with` for resources with lifecycles (the MCP client).
- `asyncio.sleep`, never `time.sleep`, inside async code.

```python
# blocking call isolated off the event loop
chunks = await asyncio.to_thread(vector_store.query, q, ticker, 2)
```

---

## 4. Pydantic v2 validation

- Request/response DTOs are `BaseModel` subclasses at the API boundary.
- Use v2 idioms only:

| Need | v2 API |
| --- | --- |
| construct from dict | `Model.model_validate(data)` |
| serialise | `model.model_dump()` / `model_dump_json()` |
| field config | `Field(..., description=...)` |
| validators | `@field_validator`, `@model_validator` |

```python
from pydantic import BaseModel, Field

class ResearchRequest(BaseModel):
    company_name: str = Field(..., min_length=1)
    ticker: str = Field(..., pattern=r"^[A-Z0-9.\-]+$")   # e.g. TCS.NS
    user_query: str = Field(..., min_length=3)
```

- Banned v1 idioms: `.dict()`, `.parse_obj()`, `class Config`, `.json()`.

---

## 5. Configuration & boundaries

- All config flows through `core/config.py` (`settings`); no module reads `os.getenv` ad hoc.
- Fail fast: `config.py` raises at import if `GOOGLE_API_KEY` is missing.
- `AgentState` lives in `core/state.py` and is the single shared contract between API and Orchestration.

---

## 6. Anti-patterns

- Cross-layer reach-around (endpoint → yfinance; node → raw chromadb client).
- Blocking I/O on the async path.
- Pydantic v1 idioms.
- `os.getenv` scattered across modules instead of `settings`.
- Untyped public functions; `Any` where a model fits.
- Business logic in `main.py`.

---

## 7. Validation

1. Import graph: no module in Storage/Tools imports from API/Orchestration.
2. Grep the async path for `requests.`/`time.sleep`/sync file reads — none on the hot path.
3. Grep for `.dict(`/`parse_obj`/`class Config` — none.
4. Every public function has a typed signature.
