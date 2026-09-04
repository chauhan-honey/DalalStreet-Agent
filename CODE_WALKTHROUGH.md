# DalalStreet-Agent — Complete Code Walkthrough & Interview Study Guide

> **How to use this document:** Open this file side-by-side with the actual code files in your editor. Read each section, then immediately open the referenced file and find the exact lines being discussed. Section 9 gives you a mind-map outline. Section 10 is a rapid-fire self-test.

> **Provenance:** this document was originally generated at the end of the Copilot Chat session that scaffolded the `.claude/` AI-team framework and then built this codebase from the `plan_by_gemini/` reference implementation. It has since been refreshed against the current code and expanded with operational details (a real ingestion run, data/API-key sourcing, sandboxed-environment setup gotchas) mined from that same chat history — see §8.1–§8.3.

---

## 1. The One-Paragraph Mental Model

Before any code, hold this picture in your head:

> A user picks a company and asks a question. The system runs that question through a **5-step pipeline** (a "graph"): first it plans *what to look for*, then it fetches evidence from **two independent sources at once** — the company's own annual-report PDF, and live stock-market numbers — then an AI "critic" compares the PDF's words against the live numbers looking for contradictions, and finally an AI "synthesizer" writes a formal report. The whole thing streams live to a web page so the user watches each step complete.

Everything below is elaboration on that one paragraph.

---

## 2. Tech Stack — What, and *Why This Specifically*

| Layer | Technology | Why THIS one (not an alternative) | When to reach for this (as a general rule) |
|---|---|---|---|
| **Orchestration** | **LangGraph** | The workflow is not a straight line — two steps run in parallel, and one step can loop back to an earlier step. A plain script or a linear LangChain "chain" cannot express a *cycle* or true parallel fan-out/fan-in with safe state merging. LangGraph models the workflow as an explicit graph of nodes + edges, exactly matching the real control flow. | When a workflow needs parallel fan-out/fan-in with safe state merging, or a runtime-decided loop/retry — not for simple linear "step 1 then step 2" chains, where a plain async function or a basic LangChain chain is simpler and sufficient (see §7.1). |
| **LLM** | **Google Gemini** (`gemini-3.6-flash`) | Has a usable **free tier** — the whole project is built under a "$0 cost" constraint. The model name is centralized in one config field (`settings.GEMINI_MODEL`), so swapping models later is a one-line change. | When the project has a hard zero/near-zero cost constraint and a "flash"-tier model's quality is sufficient for structured JSON generation and short reasoning — not when you need the strongest available reasoning or long-context recall, where a larger paid-tier model may be justified. |
| **LLM client library** | **LangChain** (`langchain-google-genai`, `langchain-core`) | Gives a uniform `ainvoke()` async interface to the LLM regardless of provider, plus message types (`HumanMessage`) — this is the glue LangGraph nodes use to talk to Gemini. | When you want a provider-agnostic async LLM interface that plugs directly into LangGraph nodes — skip it and call the provider SDK directly only if you need provider-specific features LangChain doesn't expose. |
| **Tool access for market data** | **MCP (Model Context Protocol)** via **FastMCP** | A deliberate architectural boundary: market-data access is NOT a plain Python function call — it goes through a standardized protocol as a **separate OS process**, communicating over stdio. This mirrors how production agent systems isolate/sandbox tool execution and makes the tool provider swappable without touching the graph. | When tool execution should be swappable, sandboxable, or independently versioned/deployed from the agent's reasoning code (invariant #2 in `.claude/agents/principal-architect.md`) — overkill for a single trusted in-process helper function with no isolation need. |
| **Live market data source** | **yfinance** (Yahoo Finance) | Free, no API key needed, supports Indian NSE/BSE tickers (`.NS` / `.BO` suffixes). | When you need free, no-signup market data for prototyping or a zero-cost product; move to a licensed/paid market-data API once you need guaranteed uptime, rate limits, or real-time (sub-15-minute-delay) accuracy. |
| **Document retrieval** | **RAG via ChromaDB** | An annual report is 100s of pages — too big to paste into a prompt. RAG lets the system fetch only the handful of paragraphs relevant to the current question. | When the source material is too large (or too numerous, across many documents) to fit in a single prompt/context window and you need to ground answers in retrieved, citable text rather than model memory. |
| **PDF text extraction** | **PyMuPDF** (imported as `fitz`) | Fast, reliable page-by-page text extraction; needed so every retrieved chunk can cite an exact page number. | When you need fast, dependency-light text extraction with page-level fidelity for citations; reach for OCR (e.g. Tesseract) instead only when PDFs are scanned images rather than text layers. |
| **Embeddings (text → vector)** | **sentence-transformers** `all-MiniLM-L6-v2`, run **locally on CPU** | Zero cost, zero network dependency for this step (vs. calling an embeddings API). Small and fast enough to run on a laptop. | When you need zero-cost, offline-capable embeddings and the corpus/query complexity doesn't demand a larger hosted embedding model's accuracy — swap to a hosted embeddings API when retrieval quality, not cost, becomes the bottleneck. |
| **Vector database** | **ChromaDB** (embedded, persisted to disk) | Needs no separate server process to install/manage; persists to a folder so re-ingestion isn't required after a restart; supports metadata filtering (used to isolate chunks per company). | When you need vector search with metadata filtering but the scale doesn't justify running/operating/paying for a standalone vector DB server — revisit once you need horizontal scale, multi-writer concurrency, or a managed service's SLAs. |
| **Backend API framework** | **FastAPI** | Native `async def` support (needed because every LLM/tool call is awaited), automatic request validation via Pydantic, and built-in `StreamingResponse` for the SSE endpoint. | When the API is I/O-bound (awaiting LLM/tool/network calls) and you want automatic request validation plus native streaming support — a sync framework like Flask would block the event loop the whole graph run depends on. |
| **Request validation** | **Pydantic v2** | FastAPI uses it to reject malformed requests automatically, before any application code runs. | Whenever request/response shapes need to be validated at a boundary (invariant #6) — this is close to a default in modern async Python APIs, not a situational choice. |
| **Live progress streaming** | **SSE (Server-Sent Events)**, not WebSockets | Data only flows one direction (server → browser progress updates). SSE is the simplest protocol that does exactly that over plain HTTP; WebSockets would be unnecessary complexity for a one-way stream. | When the server only needs to push one-directional progress/updates over plain HTTP — reach for WebSockets instead once the client also needs to send messages back mid-stream (e.g. cancel, adjust parameters). |
| **Frontend** | **Streamlit** | Builds a usable data-app UI in pure Python — no separate HTML/CSS/JS build step needed for a small internal tool. | When the audience is internal/small and the priority is a fast, pure-Python data-app UI — move to a proper React/Next.js frontend once you need fine-grained UI control, multi-user auth, or a public-facing polished product. |
| **Config / secrets** | **python-dotenv** | Loads a local `.env` file into the process environment so secrets never need to be typed into the shell or hardcoded. | For local/dev secret loading in any Python project — in production, prefer a real secrets manager (cloud KMS/vault) rather than a `.env` file on disk. |
| **Testing** | **pytest** + **pytest-asyncio** | Standard Python testing stack; `pytest-asyncio` is required because the code under test is `async`. | The default choice for any Python project; add `pytest-asyncio` specifically as soon as any code under test uses `async def`. |

---

## 3. Dependencies — [backend/requirements.txt](backend/requirements.txt)

```
fastapi, uvicorn, pydantic          → the web API layer
langgraph                           → the orchestration graph
langchain-google-genai              → talks to Gemini
langchain-mcp-adapters              → talks to the MCP tool server
fastmcp                             → builds the MCP tool server itself
chromadb, sentence-transformers     → the RAG / vector-search layer
pymupdf                             → PDF text extraction
yfinance                            → live market data
streamlit, requests                 → the frontend + its HTTP client
python-dotenv                       → secret/config loading
pytest, pytest-asyncio              → tests
```

`uvicorn` deserves a specific callout: it's the **ASGI server** that actually runs the FastAPI app (`uvicorn backend.app.main:app`). FastAPI defines *what* to do with a request; uvicorn is the process that *listens on a port* and hands requests to FastAPI.

---

## 4. Project Structure Map

```
DalalStreet-Agent/
├── backend/
│   ├── requirements.txt
│   └── app/
│       ├── main.py                    ← FastAPI app bootstrap (CORS, router mount)
│       ├── core/
│       │   ├── config.py              ← settings.* — the ONE place secrets/config live
│       │   └── state.py               ← AgentState — the shared dict every node reads/writes
│       ├── api/v1/
│       │   └── endpoints.py           ← POST /research/stream (SSE), GET /health
│       ├── graph.py                   ← THE ORCHESTRATION BRAIN — all 5 nodes + the graph wiring
│       ├── mcp_server/
│       │   └── stock_service.py       ← standalone MCP tool server (yfinance wrapper)
│       └── rag/
│           └── vector_store.py        ← FinancialVectorStore (PyMuPDF + ChromaDB + embeddings)
├── frontend/
│   └── app.py                         ← Streamlit UI
├── scripts/
│   └── ingest_pdf.py                  ← CLI: load one PDF into ChromaDB (run once per report)
├── data/
│   ├── annual_reports/                ← source PDFs live here
│   └── chroma_db/                     ← ChromaDB's on-disk database (auto-created)
└── tests/
    ├── test_state_reducers.py         ← proves the reducer contract in state.py
    └── test_mcp_resilience.py         ← proves _safe_info() never raises
```

**Reading order that mirrors how the system actually assembles itself** (this is also the order used in Section 6 below):
`config.py` → `state.py` → `stock_service.py` → `vector_store.py` → `graph.py` → `endpoints.py` → `main.py` → `frontend/app.py` → `ingest_pdf.py`

---

## 5. The Full Request Lifecycle (end-to-end trace)

Walk through this while tracing the corresponding file open in your editor.

```
1. User opens the Streamlit page, picks "Tata Consultancy Services" (→ ticker "TCS.NS"),
   types a question, clicks "Start Multi-Agent Audit".
        [frontend/app.py — the `if run_button:` block]

2. Streamlit POSTs JSON {company_name, ticker, user_query} to
   http://localhost:8000/api/v1/research/stream, with stream=True (keeps the
   connection open to read a live stream of responses).
        [frontend/app.py → backend/app/api/v1/endpoints.py]

3. FastAPI validates the JSON against the `ResearchRequest` Pydantic model.
   If it fails validation, the request never reaches our code (automatic 422).
        [endpoints.py: class ResearchRequest]

4. endpoints.py builds the STARTING AgentState dict — note the reducer-backed
   keys start empty ([] and {}) and iteration_count starts at 0 — and calls
   `compiled_graph.astream(initial_state)`.
        [endpoints.py: stream_financial_audit()]

5. LangGraph starts executing the compiled graph (built at the bottom of graph.py):
        START → planner_node
   planner_node calls Gemini once, asking it to output 2 RAG search queries +
   a list of metrics needed, as JSON. Writes `plan` + resets `iteration_count`.
        [graph.py: planner_node()]

6. From planner, the graph FANS OUT into two nodes that run IN PARALLEL:
     a) rag_worker_node  — parses the plan's queries, calls
        FinancialVectorStore.query() for each (via asyncio.to_thread since
        ChromaDB is blocking), collects report snippets → writes `rag_context`.
     b) market_worker_node — launches stock_service.py as a subprocess over
        MCP/stdio, calls its two tools (get_live_stock_quote,
        get_key_financial_ratios) for this ticker → writes `market_data`.
        [graph.py: rag_worker_node(), market_worker_node()]
        [rag/vector_store.py: query()]
        [mcp_server/stock_service.py: the two @mcp.tool() functions]

7. Both branches FAN IN to critic_node. LangGraph waits for BOTH to finish,
   merges their writes into the shared AgentState using the REDUCERS declared
   in state.py (operator.add for the rag_context list, operator.or_ for the
   market_data dict), THEN runs critic_node once with the combined state.
        [core/state.py: AgentState — the Annotated[...] reducer declarations]

8. critic_node sends BOTH the report snippets AND the live numbers to Gemini
   in one prompt, asking it to find contradictions. Gemini replies with a
   verdict (PASS / NEEDS_RETRY), a findings array, and feedback. The node
   increments iteration_count and FORCES verdict=PASS if the cap
   (MAX_CRITIC_ITERATIONS = 2) has been reached, regardless of what the
   model said.
        [graph.py: critic_node()]
        [core/config.py: MAX_CRITIC_ITERATIONS]

9. router_condition (a plain Python function, not an LLM call) decides the
   next node: back to rag_worker_node if NEEDS_RETRY AND under the cap,
   otherwise forward to synthesizer_node.
        [graph.py: router_condition()]

10. synthesizer_node makes ONE final Gemini call with everything gathered
    (report snippets, live numbers, audit findings) and asks for a
    structured markdown memo. Writes `final_report`. → END.
        [graph.py: synthesizer_node()]

11. As EACH node above finishes, endpoints.py's astream() loop immediately
    wraps that node's output as one SSE frame: `data: {"node": ..., "data": ...}\n\n`
    and yields it — the frontend sees progress live, not just the final result.
        [endpoints.py: `async for event in compiled_graph.astream(...)`]

12. Streamlit reads the stream line-by-line, updates a status widget per
    node, renders the market-data table and critic-findings table as they
    arrive, and on the `synthesizer` frame renders the final markdown report
    and BREAKS out of the read loop (important bugfix — see Section 8).
        [frontend/app.py: the `for line in resp.iter_lines():` loop]
```

---

## 6. File-by-File Deep Dive

### 6.1 `backend/app/core/config.py` — Configuration

**Purpose:** the single source of truth for every secret/setting. Nothing else in the codebase calls `os.getenv(...)` directly.

```python
load_dotenv()                     # reads .env into the process environment

class Settings:
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./data/chroma_db")
    MCP_SERVER_SCRIPT: str = os.getenv("MCP_SERVER_SCRIPT", "backend/app/mcp_server/stock_service.py")
    GEMINI_MODEL: str = "gemini-3.6-flash"
    MAX_CRITIC_ITERATIONS: int = 2

settings = Settings()

if not settings.GOOGLE_API_KEY:
    raise ValueError(...)          # FAIL FAST if the key is missing
```

**Why it matters / interview angle:**
- **Fail-fast validation.** If the API key is missing, the program crashes immediately at *import* time with a clear message — not 30 seconds into a run, deep inside an LLM call. This is a deliberate defensive-programming pattern.
- **Centralization.** Every tunable value (model name, retry cap, storage path) is one field here. Changing the model or the retry limit is a one-line change with zero risk of missing a scattered `os.getenv` call elsewhere.
- **Secrets never printed.** The fail-fast error message reports *that* the key is missing, never its value.

---

### 6.2 `backend/app/core/state.py` — The Shared State Contract

**Purpose:** defines `AgentState`, the one dictionary every graph node reads from and writes to.

```python
class AgentState(TypedDict):
    company_name: str
    ticker: str
    user_query: str
    plan: List[str]
    rag_context: Annotated[List[Dict[str, Any]], operator.add]   # ← REDUCER
    market_data: Annotated[Dict[str, Any], operator.or_]         # ← REDUCER
    audit_findings: List[Dict[str, Any]]
    critic_verdict: str
    critic_feedback: Optional[str]
    iteration_count: int
    final_report: Optional[str]
```

**This is the single most important concept to be able to explain in an interview.** Read it slowly:

- `TypedDict` = a plain dict at runtime, but with declared key names/types for tooling. It behaves exactly like `state["ticker"]` — no magic.
- **The problem it solves:** `rag_worker_node` and `market_worker_node` run **concurrently** (see Section 5, step 6). Both eventually return a dict to be merged into the *same* shared state. Without a rule, whichever merge LangGraph applies last would silently overwrite the other — a real, hard-to-debug class of concurrency bug.
- **The fix — reducers, via `Annotated[type, function]`:**
  - `rag_context: Annotated[List[...], operator.add]` → when two branches both write to `rag_context`, LangGraph **concatenates** the two lists (`a + b`) instead of replacing one with the other.
  - `market_data: Annotated[Dict, operator.or_]` → when two branches both write to `market_data`, LangGraph **merges** the two dicts (`{**a, **b}`).
  - Fields written by only **one** node (`critic_verdict`, `iteration_count`, `final_report`, ...) need **no reducer** — there's only ever one writer, so "last write" is trivially correct.
- **If asked "what if you forgot the reducer":** one branch's contribution would simply vanish from the final state — e.g., you'd silently lose either the RAG evidence or the market data, and the critic would reason with incomplete information, with no error or warning.

---

### 6.3 `backend/app/mcp_server/stock_service.py` — The MCP Tool Server

**Purpose:** the *only* file in the codebase allowed to call `yfinance`. Runs as its **own separate OS process**.

```python
mcp = FastMCP("DalalStreet-Market-Server")

def _safe_info(ticker: str) -> Dict[str, Any]:
    try:
        return yf.Ticker(ticker).info or {}
    except Exception:
        return {}          # NEVER raise across the MCP boundary

@mcp.tool()
def get_live_stock_quote(ticker: str) -> Dict[str, Any]:
    """..."""
    info = _safe_info(ticker)
    return {"symbol": ticker, "current_price": ..., "trailing_pe": ..., ...}

@mcp.tool()
def get_key_financial_ratios(ticker: str) -> Dict[str, Any]:
    """..."""
    info = _safe_info(ticker)
    return {"operating_margins": ..., "debt_to_equity": ..., "free_cashflow": ..., ...}

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

**Why it matters / interview angle:**
- **`@mcp.tool()` is the whole trick.** Decorating a normal typed Python function turns it into something an AI agent can discover and call by name over a standard protocol — the function's name, type hints, and docstring *are* its public API contract.
- **`transport="stdio"`** — the server doesn't listen on a network port; it reads requests from **stdin** and writes responses to **stdout**, using the MCP message format. This is why the file's docstring warns: never `print()` anything else — a stray print corrupts the protocol channel.
- **Defensive isolation (`_safe_info`).** yfinance hits a real network and can fail (bad ticker, rate limit, timeout). Since this runs behind a protocol boundary, an unhandled exception here would break the channel for the caller. So *every* failure mode collapses to `{}`, and every consuming field uses `.get(...)` → `None` rather than crashing. The critic node later treats a `None` metric as "unsupported" instead of the pipeline dying.
- **Who launches it:** `graph.py`'s `market_worker_node` — it is never imported directly; it's started as a subprocess (`sys.executable stock_service.py`) per call.

---

### 6.4 `backend/app/rag/vector_store.py` — RAG Storage

**Purpose:** turn a PDF into searchable, ticker-scoped vector chunks; answer similarity queries against them.

```python
_CHUNK_SIZE = 1000    # chars per chunk
_CHUNK_STEP = 800      # slide distance → 200-char overlap between consecutive chunks
_MIN_CHUNK_LEN = 100   # discard near-empty fragments

class FinancialVectorStore:
    def __init__(self, persist_dir=settings.CHROMA_PERSIST_DIR):
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="sentence-transformers/all-MiniLM-L6-v2"
        )
        self.collection = self.client.get_or_create_collection(
            name="annual_reports", embedding_function=self.embedding_fn
        )

    def ingest_pdf(self, file_path, ticker) -> int:
        # fitz.open() page by page → slide a 1000-char window with 200-char
        # overlap → tag each chunk {"ticker": ticker, "page": N} → upsert()

    def query(self, query_text, ticker, top_k=4) -> List[Dict]:
        # embeds query_text with the SAME model, searches, FILTERS by
        # where={"ticker": ticker}, returns [{"content": ..., "page": ...}, ...]
```

**Why it matters / interview angle:**
- **What is RAG, concretely, in this codebase:** *Retrieval*-Augmented Generation means we don't ask the LLM to "remember" the annual report. We **retrieve** the small number of paragraphs that are semantically closest to the current question, and paste only those into the prompt. This keeps the prompt small and grounds the LLM's answer in real text.
- **Chunking math:** a chunk is 1000 characters; the window slides forward by 800 each time → `1000 − 800 = 200` characters of overlap between consecutive chunks. The overlap exists so a sentence that straddles a boundary still appears whole in at least one chunk.
- **Page-level citation:** ingestion walks the PDF **page by page** (not the whole document at once) specifically so every stored chunk can carry an exact page number — this is what lets the final report cite "Page 58" for a specific claim.
- **Multi-tenant isolation in one collection.** All companies' chunks live in ONE ChromaDB collection (`"annual_reports"`), separated purely by a `ticker` metadata field. The `where={"ticker": ticker}` filter in `query()` is what guarantees a TCS question never accidentally retrieves Reliance's text — this is a deliberate design choice (one collection + metadata filter) rather than one collection per company.
- **Same embedding model at both ends.** Ingestion and query MUST use the identical embedding model, or the vectors are not comparable — this is why the model name is hardcoded once in `__init__`.
- **`upsert`, not `add`.** Chunk IDs are deterministic (`f"{ticker}_p{page}_c{chunk_id}"`), so re-ingesting the same report updates existing rows instead of creating duplicates.

---

### 6.5 `backend/app/graph.py` — The Orchestration Brain (the big one)

This file has five node functions, one router function, and the graph-assembly code at the bottom. Walk through each.

**Shared setup at the top:**
```python
llm = ChatGoogleGenerativeAI(model=settings.GEMINI_MODEL, google_api_key=..., temperature=0.1)
vector_store = FinancialVectorStore()   # ONE shared instance, reused by every call

def _parse_llm_json(raw: str) -> Dict[str, Any]:
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        return {}          # treat the LLM as an untrusted text source
```
- `temperature=0.1` → low randomness, because most prompts demand strict, repeatable JSON.
- `_parse_llm_json` is a defensive helper used by every node that expects JSON back from the model: strips markdown fences models sometimes add, and **fails soft** to `{}` rather than crashing the node on malformed output.

**Node 1 — `planner_node`:** one Gemini call; asks for 2 RAG search queries + needed metrics as JSON; writes `plan` (as raw text, parsed later) and seeds `iteration_count`.

**Node 2 — `rag_worker_node`:** parses the plan's `rag_queries`, calls `vector_store.query(...)` for each **via `asyncio.to_thread`** (ChromaDB's call is blocking/synchronous, so it's pushed to a worker thread so it doesn't freeze the async event loop that's simultaneously running `market_worker_node`). Writes `rag_context`.

**Node 3 — `market_worker_node`:** builds a `MultiServerMCPClient` pointed at `stock_service.py`, launched with `sys.executable` (so the subprocess uses the **same virtualenv**, guaranteeing it has `fastmcp`/`yfinance` installed), calls `await client.get_tools()`, resolves the two tools **by name**, invokes both for the ticker, writes `market_data = {"quote": ..., "ratios": ...}`.
> Interview note: an earlier version used `async with MultiServerMCPClient(...) as client:` — this broke when the `langchain-mcp-adapters` library changed its API to no longer support use as a context manager. The fix was to construct the client directly and call `get_tools()`. Good example of "real dependency drift you have to debug."

**Node 4 — `critic_node`:** the "self-correcting" step.
```python
res = await llm.ainvoke([HumanMessage(content=prompt)])
data = _parse_llm_json(res.text)

new_count = state.get("iteration_count", 0) + 1
verdict = data.get("verdict", "PASS")
if new_count >= settings.MAX_CRITIC_ITERATIONS:      # HARD CAP
    verdict = "PASS"

return {"critic_verdict": verdict, "critic_feedback": data.get("feedback"),
        "audit_findings": data.get("findings", []), "iteration_count": new_count}
```
- The prompt gives the LLM BOTH the RAG snippets and the live market numbers together and asks it to reconcile them, citing a page number or metric name for every finding.
- **`res.text` vs `res.content`:** newer Gemini responses can come back as a **list of content parts** rather than a plain string. `.text` is the safe accessor that normalizes both shapes — another real dependency-drift bug that was hit and fixed.
- **The hard cap is enforced in code, not by trusting the LLM.** No matter what the model says, once `iteration_count` reaches `MAX_CRITIC_ITERATIONS` (2), `verdict` is force-set to `"PASS"`. This guarantees the loop terminates and bounds both wall-clock time and Gemini API cost — you should be ready to explain *why an LLM-driven loop needs a deterministic, non-LLM stopping condition*.

**Node 5 — `synthesizer_node`:** one final Gemini call combining everything (RAG snippets, market numbers, audit findings) into a structured markdown report (Executive Summary → comparison table → risk flags → verdict). Writes `final_report`.

**`router_condition` (not a node — a plain function used as a conditional edge):**
```python
def router_condition(state: AgentState) -> Literal["rag_worker", "synthesizer"]:
    if state.get("critic_verdict") == "NEEDS_RETRY" and state.get("iteration_count", 0) < settings.MAX_CRITIC_ITERATIONS:
        return "rag_worker"
    return "synthesizer"
```
This is what makes the workflow a *graph* rather than a *chain* — the next step is chosen at runtime from the current state, and it can point **backward**.

**Graph assembly (module-level code, runs once at import):**
```python
workflow = StateGraph(AgentState)
workflow.add_node("planner", planner_node)
... # register all 5 nodes

workflow.add_edge(START, "planner")
workflow.add_edge("planner", "rag_worker")      # fan-out edge 1
workflow.add_edge("planner", "market_worker")   # fan-out edge 2
workflow.add_edge("rag_worker", "critic")        # fan-in edge 1
workflow.add_edge("market_worker", "critic")     # fan-in edge 2

workflow.add_conditional_edges("critic", router_condition,
    {"rag_worker": "rag_worker", "synthesizer": "synthesizer"})
workflow.add_edge("synthesizer", END)

compiled_graph = workflow.compile()   # ← imported by endpoints.py
```
- Two outgoing edges from `planner` = fan-out (parallel execution).
- Two incoming edges into `critic` = fan-in (LangGraph waits for BOTH before running `critic`, applying the reducers from `state.py` when it merges).
- `add_conditional_edges` is what wires in the retry loop.

---

### 6.6 `backend/app/api/v1/endpoints.py` — HTTP + SSE Layer

```python
class ResearchRequest(BaseModel):
    company_name: str = Field(..., min_length=1)
    ticker: str = Field(..., pattern=r"^[A-Za-z0-9.\-]+$")
    user_query: str = Field(..., min_length=3)

@router.post("/research/stream")
async def stream_financial_audit(req: ResearchRequest):
    async def event_generator():
        initial_state = {...}   # rag_context=[], market_data={}, iteration_count=0
        async for event in compiled_graph.astream(initial_state):
            for node_name, node_output in event.items():
                payload = {"node": node_name, "data": node_output}
                yield f"data: {json.dumps(payload, default=str)}\n\n"
                await asyncio.sleep(0.05)
    return StreamingResponse(event_generator(), media_type="text/event-stream", ...)
```
- **Pydantic validation happens before this code runs at all** — FastAPI parses/validates the JSON body against `ResearchRequest`; a malformed request never reaches `stream_financial_audit`.
- **Why the initial state seeds empty containers:** the reducers in `state.py` (`operator.add`, `operator.or_`) need *something* to combine with on the first write — starting from `[]`/`{}` is the correct identity value.
- **`compiled_graph.astream(...)`** is an async generator that yields one event per completed node, shaped `{node_name: node_output}`. The endpoint wraps each event as one SSE line: `data: {json}\n\n` — that exact format (prefix + double newline) is the SSE spec.
- **`await asyncio.sleep(0.05)`** (not `time.sleep`) briefly yields control back to the event loop so the chunk is actually flushed to the client instead of buffered.

---

### 6.7 `backend/app/main.py` — App Bootstrap

Deliberately thin: creates the `FastAPI()` app, adds `CORSMiddleware` (so the Streamlit frontend on a different port/origin is allowed to call this API), and mounts the router under `/api/v1`. All real logic lives in `endpoints.py` and `graph.py` — this file is pure wiring.

---

### 6.8 `frontend/app.py` — Streamlit UI

- Renders company selector, query textbox, and a "Start Multi-Agent Audit" button.
- On click: POSTs to the backend with `stream=True`, then does `for line in resp.iter_lines():` to read SSE frames one at a time, updating a `st.status(...)` widget and two `st.dataframe(...)` placeholders as `market_worker`/`critic` frames arrive.
- `_coerce_to_dict(value)` — a real bug fix: the MCP tool result can come back as a **list of content parts** (e.g. `[{"type": "text", "text": "{...json...}"}]`) rather than a plain dict, depending on the MCP adapter version. This helper normalizes dict / JSON-string / list-of-parts into one flat dict so the market table renders correctly.
- On the `synthesizer` frame: renders `final_report` as markdown, then **`break`s** out of the read loop.
  > **Why the `break` matters (a genuinely interesting bug):** Streamlit only repaints the page once its top-to-bottom script finishes running. The `for line in resp.iter_lines():` loop blocks waiting for the HTTP connection to fully close. Without the explicit `break`, the script would sit there even after the useful data had already arrived, and the UI would appear stuck on a spinner. Breaking on the terminal frame (`synthesizer` or `error`) lets the script finish immediately once there's nothing more to show.

---

### 6.9 `scripts/ingest_pdf.py` — Offline Ingestion CLI

A one-off command you run per report:
```bash
python scripts/ingest_pdf.py --file data/annual_reports/TCS_FY24.pdf --ticker TCS.NS
```
Parses `--file`/`--ticker` with `argparse`, then calls `FinancialVectorStore().ingest_pdf(...)`. Note the `sys.path.insert(...)` line near the top — running a script directly puts *its own folder* on `sys.path`, not the repo root, so without this line the `from backend.app... import` would fail with `ModuleNotFoundError`. (This was a real bug hit and fixed during setup.)

---

## 7. Concept Deep-Dives (be ready to explain these unprompted)

### 7.1 Why a graph instead of a linear script?
A linear script can't express: (a) two steps that must run *concurrently*, with their results safely merged, or (b) a step that can send control *backward* to an earlier step based on a runtime decision. LangGraph's `StateGraph` + conditional edges express both directly and declaratively.

### 7.2 Reducers, restated simply
> "Two workers write to the same shared dictionary at the same time. A reducer is the rule for how to combine their two answers into one, instead of one silently overwriting the other."

### 7.3 What MCP buys you architecturally
> "Instead of the agent code calling `yfinance` directly, it calls a *tool* over a protocol. The tool happens to be implemented with yfinance today, but the agent doesn't know or care — it just knows there's a tool called `get_live_stock_quote` that takes a ticker and returns a dict. That indirection is what MCP standardizes."

### 7.4 RAG in one sentence
> "Don't ask the model to remember a 300-page document — retrieve the few paragraphs that are actually relevant to this question, and only show it those."

### 7.5 Why SSE, not polling or WebSockets
> "It's a one-way stream of progress updates over a single HTTP request/response — SSE is the minimal protocol for exactly that. Polling would mean the client repeatedly asks 'are you done yet?'; WebSockets would add bidirectional capability the app doesn't need."

### 7.6 The zero-cost design constraint, end to end
Free-tier Gemini + local CPU embeddings + embedded (not hosted) ChromaDB + free yfinance data = the entire pipeline can run with $0 in paid infrastructure, which is a stated project goal, not an accident.

---

## 8. Real Bugs Fixed (great interview material — shows debugging process)

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | `404 model not found` calling Gemini | `gemini-1.5-flash` (and later `gemini-2.5-flash`) were retired for new API keys | Listed live-available models via the SDK, switched to `gemini-3.6-flash` in `config.py` |
| 2 | `AttributeError: 'list' object has no attribute 'strip'` | Newer Gemini responses return `.content` as a list of parts, not a string | Switched every usage to `.text`, which normalizes both shapes |
| 3 | `NotImplementedError` on `async with MultiServerMCPClient(...)` | `langchain-mcp-adapters` ≥0.1 dropped context-manager support | Construct the client directly, call `await client.get_tools()` |
| 4 | `ModuleNotFoundError: No module named 'backend'` running the ingest script | Running a script directly puts its own folder on `sys.path`, not the repo root | Added `sys.path.insert(...)` for the repo root at the top of `ingest_pdf.py` |
| 5 | Streamlit UI spins forever even though the backend finished | The SSE-reading loop blocks on connection close, and Streamlit only repaints after its script ends | `break` out of the loop on the terminal `synthesizer`/`error` frame |
| 6 | Market data table wouldn't render / crashed | MCP tool payload came back list-wrapped, not a plain dict | Added `_coerce_to_dict()` to normalize dict / JSON-string / list-of-parts shapes |

### 8.1 Verified End-to-End Run

The system has actually been run against real data, not just synthetic test PDFs. Per the build-session history, three real FY25-26 annual reports were ingested:

| Ticker | Chunks ingested |
|---|---|
| TCS.NS | 1,506 |
| RELIANCE.NS | 1,491 |
| INFY.NS | 1,781 |

A full graph run for TCS.NS produced a grounded audit memo with page + metric citations. One concrete finding from that run illustrates exactly what the critic loop is for: the annual report **claimed a 25.0% operating margin**, but the live `get_key_financial_ratios` figure was **23.96%** — the critic node flagged this as a **CONTRADICTION**, citing the report's page 58 alongside the live number. This is the "qualitative claim vs. quantitative reality" reconciliation described in §1, made concrete.

### 8.2 Sourcing Real Inputs

Two inputs have to come from outside the codebase before a real run is possible, and the repo docs describe *how* to ingest/configure them but not *where they come from*:

- **Annual report PDFs** — sourced in practice from [Screener.in](https://www.screener.in), the company's own investor-relations page, or BSE/NSE regulatory filings. Any of these works because `scripts/ingest_pdf.py` only needs a local PDF path — the source doesn't need to be programmatic.
- **`GOOGLE_API_KEY`** — obtained from [Google AI Studio](https://aistudio.google.com/apikey), which issues free-tier Gemini keys. This is the credential `core/config.py` fails fast on if it's missing (§6.1).

### 8.3 Sandboxed Dev Environments

If you're setting this project up inside a network-sandboxed agent environment (TLS/SOCKS-intercepting proxy), be aware: PyPI installs, the Hugging Face Hub download of `sentence-transformers/all-MiniLM-L6-v2`, and live Gemini API calls all failed under such a proxy during this project's own setup. Those three steps — `pip install`, first embedding-model load, and any Gemini call — had to be run **unsandboxed** (direct network access) to succeed. Worth checking early if setup mysteriously hangs or times out on install/ingest/first-run.

---

## 9. Mind-Map Outline (copy into a mind-mapping tool)

```
DalalStreet-Agent
├── Purpose: audit annual-report claims vs live market fundamentals
├── Tech Stack
│   ├── Orchestration: LangGraph (StateGraph, nodes, conditional edges)
│   ├── LLM: Google Gemini (gemini-3.6-flash) via LangChain
│   ├── Tool access: MCP / FastMCP (stdio subprocess)
│   ├── Market data: yfinance
│   ├── Retrieval: RAG — ChromaDB + sentence-transformers (local embeddings)
│   ├── PDF parsing: PyMuPDF (fitz)
│   ├── API: FastAPI + Pydantic v2 + SSE (StreamingResponse)
│   └── Frontend: Streamlit
├── Core Concepts
│   ├── AgentState (TypedDict) — shared memory across nodes
│   ├── Reducers — operator.add (lists) / operator.or_ (dicts) for parallel writes
│   ├── Fan-out / fan-in — planner → {rag_worker, market_worker} → critic
│   ├── Conditional edge / retry loop — router_condition, MAX_CRITIC_ITERATIONS=2
│   └── Chunking — 1000-char window, 800-char stride, 200-char overlap
├── The 5 Nodes
│   ├── 1. planner_node — LLM: query → search plan (JSON)
│   ├── 2. rag_worker_node — search ingested PDF, ticker-scoped
│   ├── 3. market_worker_node — MCP call → live quote + ratios
│   ├── 4. critic_node — LLM: reconcile claims vs numbers, PASS/NEEDS_RETRY
│   └── 5. synthesizer_node — LLM: write final markdown report
├── Request Lifecycle
│   ├── Streamlit → POST /research/stream
│   ├── FastAPI validates (Pydantic) → builds initial AgentState
│   ├── compiled_graph.astream() runs the graph, node by node
│   ├── Each node's output → one SSE frame → browser
│   └── Streamlit renders live progress + tables + final report
├── File Map
│   ├── core/config.py — settings, secrets, fail-fast
│   ├── core/state.py — AgentState + reducers
│   ├── mcp_server/stock_service.py — the MCP tool server
│   ├── rag/vector_store.py — FinancialVectorStore (ingest + query)
│   ├── graph.py — all 5 nodes + graph wiring + compiled_graph
│   ├── api/v1/endpoints.py — SSE endpoint
│   ├── main.py — FastAPI bootstrap
│   ├── frontend/app.py — Streamlit UI
│   └── scripts/ingest_pdf.py — offline ingestion CLI
└── Real Bugs Fixed (dependency drift, async/API changes) — see Section 8
    ├── 8.1 Verified end-to-end run — TCS.NS/RELIANCE.NS/INFY.NS ingested, real contradiction found (25.0% claimed vs 23.96% actual margin)
    ├── 8.2 Sourcing real inputs — PDFs from Screener.in/IR pages/BSE-NSE, API key from Google AI Studio
    └── 8.3 Sandboxed dev environments — pip/HF Hub/Gemini calls need unsandboxed network access
```

---

## 10. Rapid Self-Test (cover the answer, then check)

1. **Q:** Why can't `rag_worker_node` and `market_worker_node` just both write directly to `state["rag_context"]` and `state["market_data"]` without any special annotation?
   **A:** Because they run concurrently and both merge into the same shared state — without a reducer, one branch's write would silently overwrite the other's.

2. **Q:** What guarantees the critic/retry loop cannot run forever?
   **A:** A plain integer counter (`iteration_count`) checked against `MAX_CRITIC_ITERATIONS = 2`, enforced in code (not left to the LLM) — checked in both `critic_node` (forces `PASS`) and `router_condition` (won't route back once at the cap).

3. **Q:** Why does `market_worker_node` launch a separate process instead of importing `yfinance` directly?
   **A:** To go through the MCP tool boundary — decoupling "what tool is called" from "how it's implemented," matching how production agent systems isolate tool execution.

4. **Q:** Why is `rag_context` a `List[Dict]` with `operator.add`, but `critic_verdict` has no reducer at all?
   **A:** `rag_context` is written by a node in a parallel branch (needs merge rules); `critic_verdict` has exactly one writer (`critic_node`), so there's nothing to reconcile.

5. **Q:** What does the `where={"ticker": ticker}` filter in `vector_store.query()` prevent?
   **A:** A search for one company (e.g. TCS) accidentally returning another company's (e.g. Reliance's) report chunks, since all companies share one ChromaDB collection.

6. **Q:** Why `asyncio.to_thread(vector_store.query, ...)` instead of just `await vector_store.query(...)`?
   **A:** ChromaDB's query call is synchronous/blocking; running it directly inside an `async def` would freeze the event loop that's simultaneously running `market_worker_node`. `to_thread` offloads it to a worker thread.

7. **Q:** Why SSE instead of returning the final report in one plain JSON response?
   **A:** The workflow takes real time (multiple LLM calls); SSE streams progress live per node so the user sees what's happening instead of staring at a blank screen for 20–60 seconds.

8. **Q:** What's the actual difference between `res.content` and `res.text` on a Gemini response, and why does it matter here?
   **A:** `.content` can be either a plain string or a list of content-part dicts depending on the model/version; `.text` normalizes both to a plain string — using `.content` directly broke when a newer model returned the list form.
