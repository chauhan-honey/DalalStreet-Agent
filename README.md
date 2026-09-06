# DalalStreet-Agent

Autonomous multi-agent financial-auditing engine for the Indian capital markets (NSE & BSE). It ingests corporate Annual Reports (PDFs), retrieves live equity fundamentals over the Model Context Protocol, orchestrates specialized sub-agents with a LangGraph state machine, and runs a self-correcting Critic/Auditor loop to flag contradictions between qualitative management claims and quantitative reality.

**100% zero-cost pipeline:** Google Gemini 1.5 Flash (free tier) · local `sentence-transformers/all-MiniLM-L6-v2` CPU embeddings · embedded persistent ChromaDB.

## Architecture

```
START → planner → ┬─ rag_worker ─┐
                  └─ market_worker┘→ critic → {NEEDS_RETRY & <2 → rag_worker | else → synthesizer} → END
```

- **API** — FastAPI + SSE (`backend/app/api`, `backend/app/main.py`)
- **Orchestration** — LangGraph state machine (`backend/app/graph.py`)
- **Tools** — FastMCP market server over stdio (`backend/app/mcp_server/stock_service.py`)
- **Storage** — ChromaDB + PyMuPDF (`backend/app/rag/vector_store.py`)
- **Frontend** — Streamlit dashboard (`frontend/app.py`)

## Quick start

Dependencies are split by service — the backend and frontend containers ship
independently and don't need each other's packages.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # backend runtime deps + pytest, for local dev
cp .env.example .env                  # set GOOGLE_API_KEY

python scripts/ingest_pdf.py --file data/annual_reports/TCS_FY24.pdf --ticker TCS.NS
uvicorn backend.app.main:app --reload --port 8000

# in a second terminal (frontend only needs its own small requirements file):
pip install -r frontend/requirements.txt
streamlit run frontend/app.py --server.port 8501
```

Full walkthrough: [.claude/workflows/implementation-runbook.md](.claude/workflows/implementation-runbook.md).

## Running with Docker

The backend is also packaged as a self-contained Docker image (the ingested
`data/chroma_db/` vector store and source PDFs are baked in at build time —
see the [Dockerfile](Dockerfile) for why):

```bash
docker build -t dalalstreet-agent .
docker run --rm -p 8000:8000 --env-file .env dalalstreet-agent
```

## AI engineering team

This repo ships a reusable AI engineering team and knowledge framework under [.claude/](.claude/) — role-based sub-agents, skill handbooks, and workflows. Start at [.claude/CLAUDE.md](.claude/CLAUDE.md).

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```
