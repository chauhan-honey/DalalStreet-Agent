# DalalStreet-Agent Project Context

## Project Overview
**DalalStreet-Agent** - Autonomous multi-agent financial auditing engine for Indian capital markets (NSE & BSE). System ingests corporate Annual Reports (PDFs), queries real-time financial market fundamentals via MCP, coordinates specialized sub-agents via LangGraph, and applies self-correcting Critic/Auditor loop to flag discrepancies.

## Architecture
- **Orchestration**: LangGraph state machine with explicit branching, parallel data retrieval, cycle-based self-healing
- **Tooling**: Model Context Protocol (MCP) for real-time equity data via FastMCP
- **Verification**: Critic/Auditor loop evaluating qualitative statements vs quantitative metrics
- **Cost**: 100% zero cost - Google Gemini 1.5 Flash (Free Tier), local sentence-transformers CPU embeddings, embedded ChromaDB

## Tech Stack
- **Backend**: FastAPI, Python
- **LLM**: Google Gemini 1.5 Flash (Free Tier via Google AI Studio)
- **Orchestration**: LangGraph, LangChain
- **Vector DB**: ChromaDB with sentence-transformers (all-MiniLM-L6-v2)
- **Market Data**: yfinance via FastMCP server
- **PDF Processing**: PyMuPDF (fitz)
- **Frontend**: Streamlit
- **Protocol**: SSE Streaming for real-time updates

## Project Structure
```
dalalstreet-agent/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                   # FastAPI root & CORS
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   └── v1/
│   │   │       ├── __init__.py
│   │   │       └── endpoints.py      # SSE streaming routes
│   │   ├── core/
│   │   │   ├── __init__.py
│   │   │   ├── config.py             # Settings, env parsing
│   │   │   └── state.py              # LangGraph AgentState TypedDict
│   │   ├── mcp_server/
│   │   │   ├── __init__.py
│   │   │   └── stock_service.py      # FastMCP tool server wrapping yfinance
│   │   ├── rag/
│   │   │   ├── __init__.py
│   │   │   └── vector_store.py       # PyMuPDF parser & ChromaDB store
│   │   └── graph.py                  # LangGraph compiled state machine
│   └── requirements.txt
├── frontend/
│   └── app.py                        # Streamlit interactive UI
├── data/
│   ├── annual_reports/               # PDF directory
│   └── chroma_db/                    # Local persistent ChromaDB
├── .env.example
├── .gitignore
└── README.md
```

## LangGraph Workflow (5 Nodes)
1. **Planner Node**: Formulates PDF RAG search queries, identifies quantitative metrics
2. **RAG Worker Node**: ChromaDB Top-K semantic chunks retrieval
3. **Market Worker Node**: FastMCP server with yfinance (NSE/BSE data)
4. **Critic/Auditor Node**: Cross-examines qualitative disclosures vs live fundamentals, flags contradictions, implements retry loop (max 2 iterations)
5. **Synthesizer Node**: Compiles structured dossier with verified citations

## State Management (AgentState)
- company_name: str
- ticker: str (e.g., "TCS.NS", "RELIANCE.NS", "INFY.NS")
- user_query: str
- plan: List[str] (query decomposition)
- rag_context: List[Dict] (accumulated via operator.add)
- market_data: Dict (merged via operator.or_)
- audit_findings: List[Dict]
- critic_verdict: str ("PASS" | "NEEDS_RETRY" | "FAIL")
- critic_feedback: Optional[str]
- iteration_count: int
- final_report: Optional[str]

## MCP Server Tools
- `get_live_stock_quote(ticker)`: Real-time price, market cap, 52-week range, P/E ratios
- `get_key_financial_ratios(ticker)`: Profitability, operating margins, leverage, cash flow fundamentals

## Dependencies
```
fastapi>=0.110.0
uvicorn>=0.28.0
pydantic>=2.6.0
langgraph>=0.2.0
langchain-google-genai>=2.0.0
langchain-mcp-adapters>=0.1.0
fastmcp>=0.1.0
chromadb>=0.5.0
sentence-transformers>=2.5.0
pymupdf>=1.23.0
yfinance>=0.2.36
streamlit>=1.32.0
python-dotenv>=1.0.1
requests>=2.31.0
```

## Environment Variables
```
GOOGLE_API_KEY=your_gemini_api_key
CHROMA_PERSIST_DIR=./data/chroma_db
MCP_SERVER_SCRIPT=backend/app/mcp_server/stock_service.py
```

## Execution Commands
- Setup: `python -m venv .venv && source .venv/bin/activate && pip install -r backend/requirements.txt`
- PDF Ingestion: `python scripts/ingest_pdf.py`
- Backend Server: `uvicorn backend.app.main:app --reload --port 8000`
- Frontend Server: `streamlit run frontend/app.py --server.port 8501`