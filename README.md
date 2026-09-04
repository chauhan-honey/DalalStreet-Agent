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

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
cp .env.example .env            # set GOOGLE_API_KEY

python scripts/ingest_pdf.py --file data/annual_reports/TCS_FY24.pdf --ticker TCS.NS
uvicorn backend.app.main:app --reload --port 8000
streamlit run frontend/app.py --server.port 8501
```

Full walkthrough: [.claude/workflows/implementation-runbook.md](.claude/workflows/implementation-runbook.md).

## AI engineering team

This repo ships a reusable AI engineering team and knowledge framework under [.claude/](.claude/) — role-based sub-agents, skill handbooks, and workflows. Start at [.claude/CLAUDE.md](.claude/CLAUDE.md).

## Tests

```bash
pytest -q
```
