"""
ORCHESTRATION BRAIN  ──  backend/app/graph.py
=============================================

WHAT THIS FILE IS
    The heart of the project. It wires together every step of the analysis into a
    workflow (a "graph") using the LangGraph framework, and it is the only file
    that talks to the LLM (Google Gemini). When the API receives a request, it
    runs THIS graph from start to finish.

WHAT IS LANGGRAPH (plain-English)
    LangGraph lets you describe a workflow as a set of "nodes" (steps) connected
    by "edges" (arrows). All nodes share one dictionary of data — our `AgentState`
    (defined in core/state.py). Each node:
        - receives the current state,
        - does some work (call the LLM, search the PDF, fetch stock data),
        - returns a SMALL dict of just the keys it wants to update.
    LangGraph merges those updates into the shared state and follows the edges to
    the next node. Edges can be conditional (branch based on the state) and can
    even loop back, which is how our retry loop works.

THE WORKFLOW (what runs, in order)
        START
          │
          ▼
        planner            – ask the LLM to break the question into sub-queries
          │  (fans out into two nodes that run IN PARALLEL)
          ├───────────────┬───────────────┐
          ▼               ▼                │
        rag_worker     market_worker       │
        (search PDF)   (fetch live data)   │
          └───────────────┴────────────────┘  (both fan back IN to the critic)
          ▼
        critic             – compare the report's words vs the real numbers
          │  (conditional edge, decided by router_condition)
          ├── needs more evidence & under the retry cap ──► back to rag_worker
          ▼
        synthesizer        – write the final markdown report
          ▼
         END

HOW OTHER FILES USE THIS
    endpoints.py imports `compiled_graph` (built at the bottom) and calls
    `compiled_graph.astream(initial_state)` to run the workflow and stream each
    node's output to the browser as it happens.

DEPENDENCIES THIS FILE PULLS IN
    - settings            (core/config.py)        – model name, key, retry cap, MCP path
    - AgentState          (core/state.py)         – the shared-state shape
    - FinancialVectorStore(rag/vector_store.py)   – PDF search (used by rag_worker)
    - the MCP server      (mcp_server/stock_service.py) – launched by market_worker
"""
import asyncio
import json
import os
import sys
from typing import Any, Dict, Literal

from langchain_core.messages import HumanMessage            # wraps a user prompt for the LLM
from langchain_google_genai import ChatGoogleGenerativeAI   # the Gemini chat client
from langchain_mcp_adapters.client import MultiServerMCPClient  # talks to MCP tool servers
from langgraph.graph import END, START, StateGraph          # graph building blocks

from backend.app.core.config import settings
from backend.app.core.state import AgentState
from backend.app.rag.vector_store import FinancialVectorStore

# The LLM client, created ONCE and reused by every node that needs Gemini.
# temperature=0.1 keeps answers focused and repeatable (low randomness), which
# matters because most nodes ask the model to return strict JSON.
llm = ChatGoogleGenerativeAI(
    model=settings.GEMINI_MODEL,
    google_api_key=settings.GOOGLE_API_KEY,
    temperature=0.1,
)

# One shared vector store (opens the on-disk ChromaDB + loads the embedding model).
# Created once here so every rag_worker call reuses the same connection.
vector_store = FinancialVectorStore()


def _parse_llm_json(raw: str) -> Dict[str, Any]:
    """Safely turn an LLM's text answer into a Python dict.

    LLMs are asked to reply with pure JSON, but they often wrap it in a markdown
    code fence like ```json ... ```. This helper strips those fences and parses
    the JSON. If parsing still fails (the model returned prose or broken JSON) we
    return an empty dict {} instead of crashing — the calling node then falls
    back to sensible defaults. Treat the model as an untrusted text source.
    """
    # Remove the markdown fences the model sometimes adds around JSON.
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, TypeError):
        # Never let bad model output break a node.
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 1 — PLANNER: turn the user's question into a concrete search plan.
# ─────────────────────────────────────────────────────────────────────────────
async def planner_node(state: AgentState) -> dict:
    """First step. Asks Gemini to decompose the query into PDF search queries.

    Reads:  user_query, company_name, ticker
    Writes: plan (the LLM's JSON answer, stored as text), iteration_count (=0)
    Next:   fans out to BOTH rag_worker and market_worker (they run in parallel).
    """
    # Build the instruction sent to the LLM. We ask for two search queries plus
    # the list of financial metrics that will be needed to answer the question.
    prompt = f"""
    Analyze the user research query: "{state['user_query']}" for company: {state['company_name']} ({state['ticker']}).
    Generate a JSON plan with:
    1. "rag_queries": Array of 2 distinct search queries to find MD&A commentary in the Annual Report.
    2. "metrics_needed": Array of financial metric names.
    Respond ONLY in valid JSON. No prose, no markdown fences.
    """
    # `await llm.ainvoke(...)` sends the prompt to Gemini and waits for the reply.
    res = await llm.ainvoke([HumanMessage(content=prompt)])
    # Use res.text (the plain string form). Newer Gemini models return res.content
    # as a list of parts, so res.text is the safe way to get the text either way.
    # Save the raw JSON text (rag_worker parses it later) and start the loop
    # counter at 0 so the critic's retry logic has a value to increment.
    return {"plan": [res.text], "iteration_count": state.get("iteration_count", 0)}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 2 — RAG WORKER: search the annual-report PDF for relevant passages.
# (Runs in parallel with the market worker.)
# ─────────────────────────────────────────────────────────────────────────────
async def rag_worker_node(state: AgentState) -> dict:
    """Retrieves the report snippets that best match the planner's search queries.

    Reads:  plan (parsed to get rag_queries), ticker
    Writes: rag_context — a list of {content, page} snippets. Because this key has
            an `operator.add` reducer in AgentState, the list is APPENDED into the
            shared state (safe even though market_worker writes at the same time).
    """
    # The planner stored its answer as JSON text; parse it back into a dict.
    plan = _parse_llm_json(state["plan"][0])
    results: list[dict[str, Any]] = []
    # Run each planned search query against the vector store for THIS ticker.
    for q in plan.get("rag_queries", []):
        # vector_store.query talks to ChromaDB, which is a blocking (non-async)
        # call. asyncio.to_thread runs it on a background thread so it does not
        # freeze the async event loop (which is serving other work concurrently).
        matches = await asyncio.to_thread(vector_store.query, q, state["ticker"], 2)
        results.extend(matches)
    return {"rag_context": results}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 3 — MARKET WORKER: fetch live stock fundamentals via the MCP tool server.
# (Runs in parallel with the RAG worker.)
# ─────────────────────────────────────────────────────────────────────────────
async def market_worker_node(state: AgentState) -> dict:
    """Calls the two MCP tools to get live price + fundamentals for the ticker.

    Reads:  ticker
    Writes: market_data = {"quote": ..., "ratios": ...}. Its `operator.or_` reducer
            merges this dict into the shared state alongside rag_worker's write.
    How:    Launches mcp_server/stock_service.py as a separate process and talks
            to it over stdio using the MCP protocol (see that file's header).
    """
    # The MCP server is a script path in settings; make it absolute before launch.
    server_path = os.path.abspath(settings.MCP_SERVER_SCRIPT)
    # Newer langchain-mcp-adapters (>=0.1) no longer supports `async with` here.
    # Construct the client, then get_tools() starts the stdio server subprocess
    # and returns its tools. sys.executable ensures the subprocess uses THIS same
    # virtualenv interpreter (so it has fastmcp + yfinance installed).
    client = MultiServerMCPClient({
        "dalalstreet_mcp": {
            "transport": "stdio",          # communicate over standard input/output
            "command": sys.executable,     # the Python that runs the server subprocess
            "args": [server_path],         # the script to run
        }
    })
    # Ask the server which tools it offers, then pick the two we need by name
    # (names come from the @mcp.tool() functions in stock_service.py).
    tools = await client.get_tools()
    quote_tool = next(t for t in tools if t.name == "get_live_stock_quote")
    ratio_tool = next(t for t in tools if t.name == "get_key_financial_ratios")

    # Invoke each tool with the ticker; the server runs it and returns a dict.
    quote = await quote_tool.ainvoke({"ticker": state["ticker"]})
    ratios = await ratio_tool.ainvoke({"ticker": state["ticker"]})

    # Package both results under one key; the reducer merges it into market_data.
    return {"market_data": {"quote": quote, "ratios": ratios}}


# ─────────────────────────────────────────────────────────────────────────────
# NODE 4 — CRITIC / AUDITOR: reconcile what the company SAYS vs the real NUMBERS.
# This is the "self-correcting" step and the one that can trigger a retry.
# ─────────────────────────────────────────────────────────────────────────────
async def critic_node(state: AgentState) -> dict:
    """Compares the report's qualitative claims against the live fundamentals.

    Reads:  rag_context (report snippets), market_data (numbers), user_query
    Writes: critic_verdict ("PASS"/"NEEDS_RETRY"), audit_findings, critic_feedback,
            iteration_count (incremented).
    Loop:   If the LLM says "NEEDS_RETRY", router_condition (below) sends the flow
            back to rag_worker for another retrieval attempt — but only while
            iteration_count is under MAX_CRITIC_ITERATIONS, so it can never loop
            forever.
    """
    # Ask the LLM to act as an auditor: does the evidence support the claims, and
    # do any numbers contradict management's words? We require a strict JSON reply.
    prompt = f"""
    You are an expert Indian Equity Research Auditor.
    Cross-examine the qualitative disclosures against the live fundamental ratios.

    Qualitative Disclosures (with page citations):
    {state['rag_context']}

    Live Market Fundamentals:
    {json.dumps(state['market_data'], default=str)}

    Query: "{state['user_query']}"

    Tasks:
    1. Check whether the retrieved citations actually answer the query.
    2. Flag numerical contradictions (e.g. claimed margin expansion vs a declining operating margin).
    3. Every finding must cite a page number or a named metric; unsupported claims are labelled "unsupported".
    Return a JSON object with keys:
      - "verdict": "PASS" or "NEEDS_RETRY"
      - "findings": array of objects with keys claim, evidence, metric, status
      - "feedback": actionable retrieval instruction if NEEDS_RETRY, else null
    Respond ONLY in valid JSON. No prose, no markdown fences.
    """
    res = await llm.ainvoke([HumanMessage(content=prompt)])
    # res.text handles both plain-string and list-of-parts response content.
    data = _parse_llm_json(res.text)

    # Increment the loop counter for THIS pass. Whatever the model says, the loop
    # is bounded by the two lines below.
    new_count = state.get("iteration_count", 0) + 1
    verdict = data.get("verdict", "PASS")
    # HARD SAFETY CAP: once we have looped MAX_CRITIC_ITERATIONS times, force PASS
    # so we always move on to the report. This bounds both time and API cost.
    if new_count >= settings.MAX_CRITIC_ITERATIONS:
        verdict = "PASS"

    return {
        "critic_verdict": verdict,
        "critic_feedback": data.get("feedback"),      # guidance for the retry, if any
        "audit_findings": data.get("findings", []),   # the per-claim audit table
        "iteration_count": new_count,
    }


# ─────────────────────────────────────────────────────────────────────────────
# NODE 5 — SYNTHESIZER: write the final human-readable report. (Last step.)
# ─────────────────────────────────────────────────────────────────────────────
async def synthesizer_node(state: AgentState) -> dict:
    """Compiles everything gathered so far into a formal markdown memo.

    Reads:  user_query, company_name, ticker, rag_context, market_data, audit_findings
    Writes: final_report (markdown text). endpoints.py streams this to the UI,
            which renders it as the finished research memo.
    """
    # Feed all gathered evidence into one prompt and ask for a structured report.
    # We explicitly forbid inventing figures so the output stays grounded.
    prompt = f"""
    Compile a formal Equity Audit & Research Memorandum.
    Query: {state['user_query']}
    Company: {state['company_name']} ({state['ticker']})

    Qualitative Disclosures (with Citations):
    {state['rag_context']}

    Quantitative Fundamentals:
    {json.dumps(state['market_data'], default=str)}

    Auditor Discrepancy Findings:
    {json.dumps(state['audit_findings'], default=str)}

    Every claim must cite a page number or a named metric. Do not invent figures.
    Structure the report as follows:
    # Executive Summary
    ## Management Claims vs Actual Fundamentals (Markdown Comparison Table)
    ## Risk Flags & Discrepancies
    ## Valuation Summary & Final Verdict
    """
    res = await llm.ainvoke([HumanMessage(content=prompt)])
    # res.text is the plain markdown string (res.content may be a list of parts).
    return {"final_report": res.text}


# ─────────────────────────────────────────────────────────────────────────────
# ROUTER — the decision function for the conditional edge after the critic.
# It does NOT change state; it just returns the NAME of the next node to run.
# ─────────────────────────────────────────────────────────────────────────────
def router_condition(state: AgentState) -> Literal["rag_worker", "synthesizer"]:
    """Decide where to go after the critic: retry retrieval, or finish.

    Returns "rag_worker" (loop back for more evidence) only when BOTH are true:
      - the critic asked for a retry (verdict == "NEEDS_RETRY"), AND
      - we have not yet hit the retry cap (iteration_count < MAX_CRITIC_ITERATIONS).
    Otherwise returns "synthesizer" to write the final report. The counter check
    is what guarantees the loop always terminates.
    """
    if (
        state.get("critic_verdict") == "NEEDS_RETRY"
        and state.get("iteration_count", 0) < settings.MAX_CRITIC_ITERATIONS
    ):
        return "rag_worker"
    return "synthesizer"


# ─────────────────────────────────────────────────────────────────────────────
# GRAPH ASSEMBLY — connect the nodes above into the runnable workflow.
# This block runs ONCE when the module is imported.
# ─────────────────────────────────────────────────────────────────────────────
# StateGraph is told the shape of the shared data (AgentState) so it knows which
# keys have reducers for the parallel merge.
workflow = StateGraph(AgentState)

# Register each node function under a string name used by the edges below.
workflow.add_node("planner", planner_node)
workflow.add_node("rag_worker", rag_worker_node)
workflow.add_node("market_worker", market_worker_node)
workflow.add_node("critic", critic_node)
workflow.add_node("synthesizer", synthesizer_node)

# START is the built-in entry point. From the planner we add TWO outgoing edges,
# which makes rag_worker and market_worker run in parallel (the "fan-out").
workflow.add_edge(START, "planner")
workflow.add_edge("planner", "rag_worker")
workflow.add_edge("planner", "market_worker")

# Both workers point at the critic (the "fan-in"). LangGraph automatically waits
# for BOTH to finish, applies the reducers to merge their outputs, then runs critic.
workflow.add_edge("rag_worker", "critic")
workflow.add_edge("market_worker", "critic")

# After the critic, the next node is chosen at runtime by router_condition. The
# mapping translates the router's return value into an actual node name.
workflow.add_conditional_edges(
    "critic",
    router_condition,
    {"rag_worker": "rag_worker", "synthesizer": "synthesizer"},
)

# The synthesizer is the final step; END is the built-in terminal marker.
workflow.add_edge("synthesizer", END)

# Compile the description above into an executable object. endpoints.py imports
# THIS and calls compiled_graph.astream(...) to run a request end to end.
compiled_graph = workflow.compile()
