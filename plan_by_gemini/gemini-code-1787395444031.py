import json
import os
from typing import Literal
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langchain_mcp_adapters.client import MultiServerMCPClient
from backend.app.core.state import AgentState
from backend.app.core.config import settings
from backend.app.rag.vector_store import FinancialVectorStore

llm = ChatGoogleGenerativeAI(
    model="gemini-1.5-flash",
    google_api_key=settings.GOOGLE_API_KEY,
    temperature=0.1
)
vector_store = FinancialVectorStore()

# 1. Planner Node
async def planner_node(state: AgentState) -> dict:
    prompt = f"""
    Analyze the user research query: "{state['user_query']}" for company: {state['company_name']} ({state['ticker']}).
    Generate a JSON plan with:
    1. "rag_queries": Array of 2 distinct search queries to find MD&A commentary in the Annual Report.
    2. "metrics_needed": Array of financial metric names.
    Respond ONLY in valid JSON format.
    """
    res = await llm.ainvoke([HumanMessage(content=prompt)])
    clean_json = res.content.replace("```json", "").replace("```", "").strip()
    return {"plan": [clean_json], "iteration_count": state.get("iteration_count", 0)}

# 2. RAG Worker Node
async def rag_worker_node(state: AgentState) -> dict:
    plan = json.loads(state["plan"][0])
    results = []
    for q in plan.get("rag_queries", []):
        matches = vector_store.query(q, state["ticker"], top_k=2)
        results.extend(matches)
    return {"rag_context": results}

# 3. Market Worker Node (FastMCP client invocation)
async def market_worker_node(state: AgentState) -> dict:
    server_path = os.path.abspath(settings.MCP_SERVER_SCRIPT)
    async with MultiServerMCPClient({
        "dalalstreet_mcp": {
            "transport": "stdio",
            "command": "python",
            "args": [server_path]
        }
    }) as client:
        tools = await client.get_tools()
        quote_tool = next(t for t in tools if t.name == "get_live_stock_quote")
        ratio_tool = next(t for t in tools if t.name == "get_key_financial_ratios")
        
        quote = await quote_tool.ainvoke({"ticker": state["ticker"]})
        ratios = await ratio_tool.ainvoke({"ticker": state["ticker"]})
        
    return {"market_data": {"quote": quote, "ratios": ratios}}

# 4. Critic Node (Reflection & Reconciliation Loop)
async def critic_node(state: AgentState) -> dict:
    prompt = f"""
    You are an expert Indian Equity Research Auditor.
    Cross-examine the qualitative disclosures against live fundamental ratios.
    
    Qualitative Disclosures:
    {state['rag_context']}
    
    Live Market Fundamentals:
    {json.dumps(state['market_data'])}
    
    Query: "{state['user_query']}"
    
    Tasks:
    1. Check if the retrieved citations answer the prompt.
    2. Flag any numerical contradictions (e.g. margin expansion claimed vs actual declining margin ratio).
    3. Return a JSON object with keys:
       - "verdict": "PASS" or "NEEDS_RETRY"
       - "findings": array of audit findings
       - "feedback": instructions if retry needed
    """
    res = await llm.ainvoke([HumanMessage(content=prompt)])
    data = json.loads(res.content.replace("```json", "").replace("```", "").strip())
    
    new_count = state.get("iteration_count", 0) + 1
    verdict = data.get("verdict", "PASS")
    if new_count >= 2:
        verdict = "PASS"

    return {
        "critic_verdict": verdict,
        "critic_feedback": data.get("feedback"),
        "audit_findings": data.get("findings", []),
        "iteration_count": new_count
    }

# 5. Synthesizer Node
async def synthesizer_node(state: AgentState) -> dict:
    prompt = f"""
    Compile a formal Equity Audit & Research Memorandum.
    Query: {state['user_query']}
    Company: {state['company_name']} ({state['ticker']})
    
    Qualitative Disclosures (with Citations):
    {state['rag_context']}
    
    Quantitative Fundamentals:
    {json.dumps(state['market_data'])}
    
    Auditor Discrepancy Findings:
    {json.dumps(state['audit_findings'])}
    
    Structure the report as follows:
    # Executive Summary
    ## Management Claims vs Actual Fundamentals (Markdown Comparison Table)
    ## Risk Flags & Discrepancies
    ## Valuation Summary & Final Verdict
    """
    res = await llm.ainvoke([HumanMessage(content=prompt)])
    return {"final_report": res.content}

# Conditional Branching
def router_condition(state: AgentState) -> Literal["rag_worker", "synthesizer"]:
    if state.get("critic_verdict") == "NEEDS_RETRY" and state.get("iteration_count", 0) < 2:
        return "rag_worker"
    return "synthesizer"

# Compile Graph
workflow = StateGraph(AgentState)
workflow.add_node("planner", planner_node)
workflow.add_node("rag_worker", rag_worker_node)
workflow.add_node("market_worker", market_worker_node)
workflow.add_node("critic", critic_node)
workflow.add_node("synthesizer", synthesizer_node)

workflow.add_edge(START, "planner")
workflow.add_edge("planner", "rag_worker")
workflow.add_edge("planner", "market_worker")
workflow.add_edge("rag_worker", "critic")
workflow.add_edge("market_worker", "critic")

workflow.add_conditional_edges("critic", router_condition, {
    "rag_worker": "rag_worker",
    "synthesizer": "synthesizer"
})
workflow.add_edge("synthesizer", END)

compiled_graph = workflow.compile()