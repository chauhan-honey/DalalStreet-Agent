# Workflow: Implementation Runbook

> Step-by-step guide to stand up DalalStreet-Agent end to end: environment, PDF ingestion, FastMCP server, backend API, and Streamlit frontend. Commands assume repo root and Python 3.10+.

---

## 0. Prerequisites

- Python **3.10+** (`python --version`).
- A free **Google AI Studio** API key (Gemini 1.5 Flash free tier).
- macOS/Linux shell. First run downloads the local MiniLM model (~90 MB) once.

---

## 1. Environment initialisation

```bash
# from repo root
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install --upgrade pip
pip install -r backend/requirements.txt
```

Create your local secrets file from the template:

```bash
cp .env.example .env
# edit .env and set GOOGLE_API_KEY=<your key>
```

`.env` (git-ignored):
```dotenv
GOOGLE_API_KEY=your_gemini_api_key
CHROMA_PERSIST_DIR=./data/chroma_db
MCP_SERVER_SCRIPT=backend/app/mcp_server/stock_service.py
```

Sanity check — config must fail fast if the key is missing:
```bash
python -c "from backend.app.core.config import settings; print('config OK')"
```

> See [skills/secure-api-key-handling.md](../skills/secure-api-key-handling.md) for the secret-hygiene rules.

---

## 2. PDF ingestion (build the RAG index)

Drop annual-report PDFs into `data/annual_reports/`, then index them by ticker into the local persistent ChromaDB:

```bash
python scripts/ingest_pdf.py \
    --file data/annual_reports/tcs_annual_report.pdf \
    --ticker TCS.NS
```

What happens: PyMuPDF extracts text page-by-page → chunks (1000-char window, 800 stride) → local MiniLM embeddings → `upsert` into the `annual_reports` collection with `{ticker, page}` metadata.

Verify:
```bash
python -c "from backend.app.rag.vector_store import FinancialVectorStore as V; \
print(V().query('management outlook on margins', 'TCS.NS', top_k=2))"
```
Expect a list of `{"content": ..., "page": ...}` dicts. Empty list = nothing ingested for that ticker.

> Details: [skills/chromadb-hybrid-rag.md](../skills/chromadb-hybrid-rag.md).

---

## 3. FastMCP market server

The graph launches the MCP server as a `stdio` subprocess automatically, but verify it stands alone first:

```bash
python backend/app/mcp_server/stock_service.py
# starts a stdio MCP server exposing get_live_stock_quote + get_key_financial_ratios
# Ctrl-C to stop; no stdout noise should appear (stdout is the protocol channel)
```

> Details: [skills/fastmcp-tool-contracts.md](../skills/fastmcp-tool-contracts.md).

---

## 4. Backend API boot

```bash
uvicorn backend.app.main:app --reload --port 8000
```

Smoke-test the SSE stream:
```bash
curl -N -X POST http://localhost:8000/api/v1/research/stream \
  -H "Content-Type: application/json" \
  -d '{"company_name":"Tata Consultancy Services","ticker":"TCS.NS","user_query":"Do management margin claims match live fundamentals?"}'
```

Expect incremental frames, one per node:
```text
data: {"node": "planner", "data": {...}}

data: {"node": "rag_worker", "data": {...}}

data: {"node": "market_worker", "data": {...}}

data: {"node": "critic", "data": {"critic_verdict": "PASS", ...}}

data: {"node": "synthesizer", "data": {"final_report": "# Executive Summary ..."}}
```

> Framing rules: [skills/sse-streaming-protocols.md](../skills/sse-streaming-protocols.md).

---

## 5. Streamlit frontend

In a second terminal (same venv):

```bash
source .venv/bin/activate
streamlit run frontend/app.py --server.port 8501
```

Open http://localhost:8501, enter company / ticker / query, run. The UI consumes the SSE stream and lights each node up in order, shows the critic verdict badge, renders the market table and cited RAG excerpts, then the final markdown memo.

Point the UI at the backend via env (defaults to localhost):
```bash
export BACKEND_URL=http://localhost:8000
```

---

## 6. End-to-end verification checklist

- [ ] `config OK` prints (secrets load, fail-fast works).
- [ ] Ingestion returns non-empty query results for your ticker.
- [ ] MCP server starts standalone with no stdout noise.
- [ ] `curl -N` shows all 5 node frames in order.
- [ ] A margin-contradiction query surfaces a `contradiction` finding.
- [ ] Forcing repeated `NEEDS_RETRY` still terminates at `synthesizer` (≤2 loops).
- [ ] Streamlit renders live status + final memo.
- [ ] `pytest -q` green; secret-grep clean.

---

## 7. Common issues

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ValueError: GOOGLE_API_KEY is missing` | `.env` not created / key unset | `cp .env.example .env`, set the key |
| RAG returns `[]` | ticker not ingested, or filter mismatch | re-run ingestion with the exact ticker |
| MCP stream corrupt / hangs | stray `print` in server process | remove stdout prints; log to stderr |
| yfinance `None` metrics | delisted/unknown ticker or rate limit | verify ticker (`.NS`/`.BO`), retry with backoff |
| SSE frames arrive all at once | proxy buffering | set `X-Accel-Buffering: no` header |
| `429 RESOURCE_EXHAUSTED` | Gemini free-tier quota | back off; keep to ≤4 LLM calls/run |

---

## 8. Command reference

```bash
# setup
python -m venv .venv && source .venv/bin/activate && pip install -r backend/requirements.txt
# ingest
python scripts/ingest_pdf.py --file <pdf> --ticker <TICKER.NS>
# backend
uvicorn backend.app.main:app --reload --port 8000
# frontend
streamlit run frontend/app.py --server.port 8501
# tests
pytest -q
```
