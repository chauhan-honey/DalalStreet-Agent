# DalalStreet-Agent

Autonomous multi-agent financial-auditing engine for the Indian capital markets (NSE & BSE). It ingests corporate Annual Reports (PDFs), retrieves live equity fundamentals over the Model Context Protocol, orchestrates specialized sub-agents with a LangGraph state machine, and runs a self-correcting Critic/Auditor loop to flag contradictions between qualitative management claims and quantitative reality.

**100% zero-cost pipeline:** Google Gemini (`gemini-3.6-flash`, free tier — set once in [config.py](backend/app/core/config.py)) · local `sentence-transformers/all-MiniLM-L6-v2` CPU embeddings · embedded persistent ChromaDB.

## Live demo

> ⚠️ **Before you click: please run one audit, not several in a row.** This is a
> zero-cost deployment with two real free-tier limits:
> - **Gemini's free tier allows ~20 requests/day for the whole project**, and one
>   audit uses several (planner, critic, synthesizer). A handful of runs can
>   exhaust it; an error after that is the quota, not a bug, and it resets ~24h later.
> - **The backend scales to zero when idle**, so the first request after a quiet
>   period takes **60–90 seconds to cold-start**. That's expected, not a hang.
>
> The app shows the same notice at the top of the page.

- **App:** https://dalalstreet-agent.streamlit.app/
- **Backend API:** https://dalalstreet-agent-backend.nicemushroom-fed075ab.centralindia.azurecontainerapps.io (`GET /api/v1/health`)

## Architecture

```
START → planner → ┬─ rag_worker ─┐
                  └─ market_worker┘→ critic → {NEEDS_RETRY & <2 → rag_worker | else → synthesizer} → END
```

The planner fans out to two workers that run in parallel; their writes merge through declared reducers; the critic either asks for another retrieval pass (hard-capped at 2) or hands off to the synthesizer, which writes the cited memo.

### Source map

Every source file in the project, by layer (dependencies point downward only):

| Layer | File | What it does |
| --- | --- | --- |
| API | [backend/app/main.py](backend/app/main.py) | FastAPI app: CORS, rate-limiter wiring, `/docs` disabled in production |
| API | [backend/app/api/v1/endpoints.py](backend/app/api/v1/endpoints.py) | `POST /research/stream` (SSE, one frame per graph node), `GET /health`, API-key check |
| API | [backend/app/core/rate_limit.py](backend/app/core/rate_limit.py) | `slowapi` limiter (10 runs/hour per IP) protecting the Gemini quota |
| Orchestration | [backend/app/graph.py](backend/app/graph.py) | The 5-node LangGraph workflow, all Gemini prompts, bounded critic loop |
| Orchestration | [backend/app/core/state.py](backend/app/core/state.py) | `AgentState` with `operator.add` / `operator.or_` reducers for the parallel fan-in |
| Config | [backend/app/core/config.py](backend/app/core/config.py) | Settings from env vars; fails fast without `GOOGLE_API_KEY`; model ID lives here |
| Tools | [backend/app/mcp_server/stock_service.py](backend/app/mcp_server/stock_service.py) | FastMCP stdio server exposing `get_live_stock_quote` / `get_key_financial_ratios` (yfinance) |
| Storage | [backend/app/rag/vector_store.py](backend/app/rag/vector_store.py) | PyMuPDF page-tagged chunking (1000 chars, 200 overlap) + ChromaDB, ticker-filtered queries |
| Ingestion | [scripts/ingest_pdf.py](scripts/ingest_pdf.py) | CLI to ingest an annual report into the vector store |
| Frontend | [frontend/app.py](frontend/app.py) | Streamlit dashboard consuming the SSE stream |
| Evaluation | [scripts/eval_retrieval.py](scripts/eval_retrieval.py), [scripts/eval_critic.py](scripts/eval_critic.py) | Retrieval-quality and critic-accuracy evaluations (see [Evaluation](#evaluation)) |
| CI/CD | [.github/workflows/ci-cd.yml](.github/workflows/ci-cd.yml) | Test → build image → deploy (see [CI/CD](#cicd)) |
| Container | [Dockerfile](Dockerfile) | Multi-stage, CPU-only torch, non-root, healthcheck, vector store baked in |

A file-by-file deep dive, including bugs found and fixed during development, is in [CODE_WALKTHROUGH.md](CODE_WALKTHROUGH.md).

### Companies

Annual reports for **TCS, Reliance and Infosys** are ingested and baked into the backend image. The UI also accepts **any other NSE/BSE ticker**: live market data works for it, but there is no report text to audit, so the critic marks management claims as unsupported. Runtime PDF upload was deliberately left out. The backend runs on Azure Container Apps with no persistent disk and scales to zero, so an uploaded report would vanish on the next cold start. To add a company, run the ingestion script and redeploy (the CI/CD pipeline rebuilds the image on merge):

```bash
python scripts/ingest_pdf.py --file data/annual_reports/<report>.pdf --ticker <TICKER>.NS
```

## CI/CD

[.github/workflows/ci-cd.yml](.github/workflows/ci-cd.yml) runs on GitHub Actions:

1. **Every pull request and push to `main`:** install dependencies and run the full `pytest` suite.
2. **On merge to `main` only:** build the Docker image and push it to GitHub Container Registry.
3. **Then deploy** it to Azure Container Apps. Login uses Azure OIDC federated credentials, so no cloud password is stored in GitHub.

`main` is branch-protected: changes land only through pull requests with a passing test check.

## Quick start

Dependencies are split by service — the backend and frontend containers ship
independently and don't need each other's packages.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # backend + frontend deps + pytest, for local dev
cp .env.example .env                  # set GOOGLE_API_KEY

python scripts/ingest_pdf.py --file data/annual_reports/TCS_annual-report-2025-2026.pdf --ticker TCS.NS
uvicorn backend.app.main:app --reload --port 8000

# in a second terminal:
streamlit run frontend/app.py --server.port 8501
```

## Running with Docker

The backend is also packaged as a self-contained Docker image (the ingested
`data/chroma_db/` vector store and source PDFs are baked in at build time —
see the [Dockerfile](Dockerfile) for why):

```bash
docker build -t dalalstreet-agent .
docker run --rm -p 8000:8000 --env-file .env dalalstreet-agent
```

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

46 tests, none of which call Gemini or the network. External services are replaced by fakes at the module boundary.

| File | Covers |
| --- | --- |
| [tests/test_graph.py](tests/test_graph.py) | Compiled-graph topology; full runs with fake LLM/MCP/vector store; retry cap; edge cases: LLM returns prose instead of JSON (planner and critic), empty plan, ticker with no ingested report |
| [tests/test_sse_endpoint.py](tests/test_sse_endpoint.py) | SSE frame format and ordering, user-facing vs. generic error frames, no internal-detail leakage, 422 validation, 401 auth |
| [tests/test_vector_store.py](tests/test_vector_store.py) | Page-tagged ingestion, cross-ticker isolation, idempotent re-ingestion, malformed PDF, unknown ticker |
| [tests/test_frontend.py](tests/test_frontend.py) | Streamlit AppTest: no premature spinner, custom-ticker validation, payload sent + error shown, MCP payload normalisation |
| [tests/test_state_reducers.py](tests/test_state_reducers.py) | Reducer declarations and merge semantics |
| [tests/test_mcp_resilience.py](tests/test_mcp_resilience.py) | MCP tool helpers never raise across the protocol boundary |
| [tests/test_auth.py](tests/test_auth.py) | API-key dependency |

## Evaluation

### Retrieval quality — [scripts/eval_retrieval.py](scripts/eval_retrieval.py)

15 queries across the three reports, each phrased differently from the report's own wording. A page counts as relevant if it contains one of the query's anchor phrases (for example "attrition", or "buyback" / "bought back"), so relevance is defined objectively rather than judged by hand. Free to run; no Gemini calls.

| Metric | Result |
| --- | --- |
| Hit@2 (what `rag_worker` retrieves per query) | **40%** |
| Hit@4 | **53%** |
| MRR@4 | **0.41** |

**What this shows:**
- Broad, recurring topics are found reliably: operating margin, O2C, digital services, share buybacks.
- Narrow facts that appear on one or two pages are often missed: attrition, TCV, free cash flow.
- The failure mode is look-alike boilerplate. For example, "total contract value of new deals" retrieves the auditor's report on "estimated total efforts" for fixed-price contracts. MiniLM embeddings over 1000-character chunks match surface vocabulary, and a dense annual report has a lot of it.
- The metric is a lower bound, since a retrieved page can be relevant without containing the anchor phrase.

**Most promising next step:** hybrid retrieval (BM25 keyword scores fused with the dense scores), or retrieving more than 2 chunks per query and letting the critic filter them. Exact financial terms like "TCV" are exactly where keyword search beats small embedding models. The critic's retry loop already partly compensates, because it can request a second retrieval pass when the evidence doesn't answer the query.

### Critic accuracy — [scripts/eval_critic.py](scripts/eval_critic.py)

Six seeded cases with known answers: management claims that are accurate, overstated, or not checkable against the available metrics, paired with fixed fundamentals. Only the critic node runs, at one Gemini call per case.

| Case | Expected | Critic said |
| --- | --- | --- |
| Operating margin overstated (26.5% claimed vs 23.96%) | contradiction | contradiction |
| Operating margin accurate (24.0% vs 23.96%) | aligned | aligned |
| Revenue growth overstated (12% claimed vs 4.7%) | contradiction | contradiction |
| Revenue growth accurate (4.7%) | aligned | aligned |
| Leverage misstated ("debt exceeds equity" vs D/E 9.8%) | contradiction | contradiction |
| Claim with no matching metric (employee engagement) | unsupported | unsupported |

**Result: 6/6** with `gemini-3.6-flash`.

**What this shows:** given the right evidence, the critic reliably separates accurate, overstated and uncheckable claims, including near-misses like 24.0% vs 23.96% that it correctly treats as aligned. Six cases are a smoke test, not a benchmark. Retrieval is the weaker stage, which is why the end-to-end quality bottleneck sits there, not in the critic.

### End-to-end example

The documented live run on TCS flagged a claimed 25.0% operating margin against an actual 23.96% from live fundamentals. See [CODE_WALKTHROUGH.md](CODE_WALKTHROUGH.md).

## How this was built

This project was built AI-assisted, with me directing the work and making the design and scope decisions:

1. **Design:** an initial high-level design and reference implementation were drafted with Gemini. I used them as a starting point, not a spec, and they are not shipped in this repo.
2. **Engineering framework:** I set up the [.claude/](.claude/) framework before writing application code. It defines role-based agent personas (architect, tech lead, GenAI, backend, prompt, frontend, QA/security), skill handbooks, and seven non-negotiable invariants in [.claude/CLAUDE.md](.claude/CLAUDE.md), such as zero cost, MCP-only market data, a bounded critic loop, and reducer-based state merging. It works as the brief and review checklist given to AI coding assistants, so generated code is held to the same architecture every time.
3. **Implementation:** the codebase was built with AI coding assistants (GitHub Copilot Chat, then Claude Code) working against that framework, phase by phase, following [the implementation runbook](.claude/workflows/implementation-runbook.md).
4. **Review and hardening:** I tested the running app myself and drove the fixes. Examples include retired Gemini model IDs, a quota-exhaustion incident that stalled the server, concurrent-request protection in the UI, and a security review that led to the API key, rate limiting, CORS restriction and error-message sanitisation.
5. **Shipping:** every change after the initial build went through a pull request gated by CI, as described under [CI/CD](#cicd).

[CODE_WALKTHROUGH.md](CODE_WALKTHROUGH.md) is my study guide to the finished code; its §8 records real bugs found and how they were fixed.
