"""HTTP-level tests for POST /api/v1/research/stream (backend/app/api/v1/endpoints.py).

The compiled graph is swapped for a fake that yields scripted node events, so
these tests check the SSE contract the frontend depends on — frame format,
ordering, and error frames — without calling Gemini or yfinance.
"""
import json

import pytest
from fastapi.testclient import TestClient

from backend.app.api.v1 import endpoints
from backend.app.core.config import settings
from backend.app.core.rate_limit import limiter
from backend.app.graph import UnusableLLMResponse
from backend.app.main import app

URL = "/api/v1/research/stream"
BODY = {"company_name": "Tata Consultancy Services", "ticker": "TCS.NS",
        "user_query": "Are cloud margins stable?"}


class FakeGraph:
    def __init__(self, events=(), error=None):
        self.events, self.error = events, error

    async def astream(self, initial_state):
        for event in self.events:
            yield event
        if self.error:
            raise self.error


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(limiter, "enabled", False)
    monkeypatch.setattr(settings, "BACKEND_API_KEY", "")
    return TestClient(app)


def _frames(response):
    raw = response.text
    assert raw.endswith("\n\n")
    chunks = raw.split("\n\n")[:-1]
    assert all(c.startswith("data: ") for c in chunks)
    return [json.loads(c[len("data: "):]) for c in chunks]


def test_streams_one_frame_per_node_in_order(client, monkeypatch):
    events = [
        {"planner": {"plan": ["{}"], "iteration_count": 0}},
        {"market_worker": {"market_data": {"quote": {"price": 4100.0}}}},
        {"rag_worker": {"rag_context": [{"content": "x", "page": 3}]}},
        {"critic": {"critic_verdict": "PASS", "audit_findings": []}},
        {"synthesizer": {"final_report": "# Executive Summary"}},
    ]
    monkeypatch.setattr(endpoints, "compiled_graph", FakeGraph(events))
    res = client.post(URL, json=BODY)

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    frames = _frames(res)
    assert [f["node"] for f in frames] == ["planner", "market_worker", "rag_worker", "critic", "synthesizer"]
    assert frames[-1]["data"]["final_report"] == "# Executive Summary"


def test_unusable_llm_response_is_shown_to_the_user(client, monkeypatch):
    msg = "The planner (Gemini) did not return a usable search plan."
    monkeypatch.setattr(endpoints, "compiled_graph", FakeGraph(error=UnusableLLMResponse(msg)))
    frames = _frames(client.post(URL, json=BODY))

    assert frames == [{"node": "error", "data": {"message": msg}}]


def test_unexpected_errors_do_not_leak_internals(client, monkeypatch):
    boom = RuntimeError("connection to /internal/host/path failed")
    monkeypatch.setattr(endpoints, "compiled_graph",
                        FakeGraph(events=[{"planner": {"plan": ["{}"]}}], error=boom))
    frames = _frames(client.post(URL, json=BODY))

    assert frames[0]["node"] == "planner"  # progress before the failure still arrives
    assert frames[-1]["node"] == "error"
    assert frames[-1]["data"]["message"] == "An internal error occurred. Please try again."
    assert "/internal/host" not in json.dumps(frames)


@pytest.mark.parametrize("bad_body", [
    {**BODY, "ticker": "TCS.NS; rm -rf /"},
    {**BODY, "user_query": "hi"},
    {**BODY, "company_name": ""},
])
def test_invalid_requests_rejected_before_the_graph_runs(client, monkeypatch, bad_body):
    monkeypatch.setattr(endpoints, "compiled_graph", FakeGraph(error=AssertionError("must not run")))
    assert client.post(URL, json=bad_body).status_code == 422


def test_missing_api_key_rejected_when_configured(client, monkeypatch):
    monkeypatch.setattr(settings, "BACKEND_API_KEY", "correct-key")
    monkeypatch.setattr(endpoints, "compiled_graph", FakeGraph(error=AssertionError("must not run")))
    assert client.post(URL, json=BODY).status_code == 401
    assert client.post(URL, json=BODY, headers={"X-API-Key": "correct-key"}).status_code == 200
