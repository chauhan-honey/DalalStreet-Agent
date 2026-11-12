"""Integration tests for the compiled LangGraph workflow (backend/app/graph.py).

The real graph is run end to end, with only its three external dependencies
replaced by fakes: the Gemini client (`graph.llm`), the vector store
(`graph.vector_store`), and the MCP client class (`graph.MultiServerMCPClient`).
Nodes look these names up on the module at call time, so patching them is
enough — no network, no API quota, no ChromaDB access.
"""
import asyncio
import json

import pytest

from backend.app import graph
from backend.app.core.config import settings

PLAN = json.dumps({"rag_queries": ["cloud margin commentary", "cash flow commentary"],
                   "metrics_needed": ["operatingMargins"]})
CRITIC_PASS = json.dumps({
    "verdict": "PASS",
    "findings": [{"claim": "Margins expanded", "evidence": "p.12",
                  "metric": "operatingMargins", "status": "contradiction"}],
    "feedback": None,
})
CRITIC_RETRY = json.dumps({"verdict": "NEEDS_RETRY", "findings": [], "feedback": "look for segment data"})


class _Reply:
    def __init__(self, text: str):
        self.text = text


class FakeLLM:
    """Answers each node's prompt with a scripted reply and records the calls."""

    def __init__(self, planner=PLAN, critic=CRITIC_PASS, synthesizer="# Executive Summary\nok"):
        self.replies = {"planner": planner, "critic": critic, "synthesizer": synthesizer}
        self.calls: list[str] = []

    async def ainvoke(self, messages):
        prompt = messages[0].content
        if "Generate a JSON plan" in prompt:
            node = "planner"
        elif "Research Auditor" in prompt:
            node = "critic"
        else:
            node = "synthesizer"
        self.calls.append(node)
        return _Reply(self.replies[node])


class FakeVectorStore:
    def __init__(self, hits=None):
        self.hits = [{"content": "Cloud margins expanded.", "page": 12}] if hits is None else hits
        self.queries: list[tuple[str, str]] = []

    def query(self, text, ticker, top_k=4):
        self.queries.append((text, ticker))
        return list(self.hits)


class _FakeTool:
    def __init__(self, name, result):
        self.name = name
        self._result = result

    async def ainvoke(self, args):
        return {**self._result, "symbol": args["ticker"]}


class FakeMCPClient:
    def __init__(self, config):
        self.config = config

    async def get_tools(self):
        return [
            _FakeTool("get_live_stock_quote", {"price": 4100.0}),
            _FakeTool("get_key_financial_ratios", {"operatingMargins": 0.2396}),
        ]


@pytest.fixture
def fakes(monkeypatch):
    llm, store = FakeLLM(), FakeVectorStore()
    monkeypatch.setattr(graph, "llm", llm)
    monkeypatch.setattr(graph, "vector_store", store)
    monkeypatch.setattr(graph, "MultiServerMCPClient", FakeMCPClient)
    return llm, store


def _run():
    initial = {
        "company_name": "Tata Consultancy Services", "ticker": "TCS.NS",
        "user_query": "Are cloud margins stable?", "rag_context": [],
        "market_data": {}, "audit_findings": [], "iteration_count": 0,
    }
    return asyncio.run(graph.compiled_graph.ainvoke(initial))


# ---------- topology ----------

def test_topology_matches_documented_five_node_workflow():
    g = graph.compiled_graph.get_graph()
    assert set(g.nodes) == {"__start__", "planner", "rag_worker", "market_worker",
                            "critic", "synthesizer", "__end__"}
    edges = {(e.source, e.target) for e in g.edges}
    assert {
        ("__start__", "planner"),
        ("planner", "rag_worker"), ("planner", "market_worker"),   # fan-out
        ("rag_worker", "critic"), ("market_worker", "critic"),     # fan-in
        ("critic", "rag_worker"), ("critic", "synthesizer"),       # conditional retry
        ("synthesizer", "__end__"),
    } <= edges


@pytest.mark.parametrize("verdict, count, expected", [
    ("NEEDS_RETRY", 1, "rag_worker"),
    ("NEEDS_RETRY", settings.MAX_CRITIC_ITERATIONS, "synthesizer"),
    ("PASS", 1, "synthesizer"),
    (None, 0, "synthesizer"),
])
def test_router_condition(verdict, count, expected):
    assert graph.router_condition({"critic_verdict": verdict, "iteration_count": count}) == expected


def test_parse_llm_json_strips_fences_and_tolerates_prose():
    assert graph._parse_llm_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert graph._parse_llm_json("Sure! Here is your plan.") == {}


# ---------- full runs with fakes ----------

def test_happy_path_produces_cited_report(fakes):
    llm, store = fakes
    final = _run()

    assert llm.calls == ["planner", "critic", "synthesizer"]
    assert [q for q, _ in store.queries] == ["cloud margin commentary", "cash flow commentary"]
    assert all(ticker == "TCS.NS" for _, ticker in store.queries)
    assert final["rag_context"] == [{"content": "Cloud margins expanded.", "page": 12}] * 2
    assert final["market_data"]["ratios"]["operatingMargins"] == 0.2396
    assert final["market_data"]["quote"]["symbol"] == "TCS.NS"
    assert final["critic_verdict"] == "PASS"
    assert final["audit_findings"][0]["status"] == "contradiction"
    assert final["final_report"].startswith("# Executive Summary")


def test_retry_loop_is_hard_capped(fakes):
    llm, store = fakes
    llm.replies["critic"] = CRITIC_RETRY  # the critic never becomes satisfied
    final = _run()

    assert llm.calls.count("critic") == settings.MAX_CRITIC_ITERATIONS
    assert llm.calls[-1] == "synthesizer"
    assert final["critic_verdict"] == "PASS"  # forced by the cap
    # Each pass re-retrieves; operator.add accumulates instead of overwriting.
    assert len(final["rag_context"]) == 2 * settings.MAX_CRITIC_ITERATIONS


@pytest.mark.parametrize("planner_reply", [
    "I'd be happy to help analyse TCS!",          # prose instead of JSON
    json.dumps({"metrics_needed": ["pe"]}),        # JSON without rag_queries
    json.dumps({"rag_queries": []}),               # empty plan
    json.dumps({"rag_queries": "not a list"}),     # wrong type
])
def test_unusable_plan_stops_the_run_with_a_clear_error(fakes, planner_reply):
    llm, store = fakes
    llm.replies["planner"] = planner_reply
    with pytest.raises(graph.UnusableLLMResponse, match="usable search plan"):
        _run()
    assert llm.calls == ["planner"]  # nothing downstream spent quota
    assert store.queries == []


def test_critic_prose_reply_defaults_to_pass_and_still_reports(fakes):
    llm, _ = fakes
    llm.replies["critic"] = "The disclosures look broadly consistent."
    final = _run()

    assert final["critic_verdict"] == "PASS"
    assert final["audit_findings"] == []
    assert final["final_report"]


def test_ticker_with_no_ingested_report_still_completes(fakes, monkeypatch):
    # Empty ChromaDB / un-ingested ticker: retrieval finds nothing, but the run
    # still finishes on market data alone instead of crashing.
    monkeypatch.setattr(graph, "vector_store", FakeVectorStore(hits=[]))
    final = _run()

    assert final["rag_context"] == []
    assert final["market_data"]["quote"]["price"] == 4100.0
    assert final["final_report"]
