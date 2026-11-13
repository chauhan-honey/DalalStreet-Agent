"""Tests for the Streamlit frontend (frontend/app.py).

Uses Streamlit's own AppTest harness, which runs the script headlessly — no
browser and no backend needed, since none of these tests click "Start".
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

# AppTest resolves relative paths against the test file, not the repo root.
APP = str(Path(__file__).resolve().parents[1] / "frontend" / "app.py")
CUSTOM = "Other — enter an NSE/BSE ticker"


@pytest.fixture
def at():
    app = AppTest.from_file(APP, default_timeout=30)
    app.run()
    assert not app.exception
    return app


def _start_button(at):
    return next(b for b in at.button if b.label == "Start Multi-Agent Audit")


def test_first_load_shows_no_spinner_and_an_enabled_button(at):
    assert len(at.status) == 0
    assert not _start_button(at).disabled
    assert any("Free-tier demo" in i.value for i in at.info)


def test_custom_ticker_requires_valid_input(at):
    at.selectbox[0].select(CUSTOM).run()
    assert _start_button(at).disabled  # nothing entered yet

    at.text_input[0].input("HDFC Bank")
    at.text_input[1].input("HDFC;BANK").run()
    assert _start_button(at).disabled
    assert at.error and "Tickers may only contain" in at.error[0].value

    at.text_input[1].input("hdfcbank.ns").run()
    assert not _start_button(at).disabled
    assert not at.error


class _FakeStream:
    status_code = 200
    lines = [b'data: {"node": "error", "data": {"message": "planner returned no plan"}}', b""]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def raise_for_status(self):
        pass

    def iter_lines(self):
        return iter(self.lines)


def test_custom_ticker_is_sent_and_backend_error_is_shown(at, monkeypatch):
    import requests
    sent = {}

    def fake_post(url, json, **kwargs):
        sent.update(json)
        return _FakeStream()

    monkeypatch.setattr(requests, "post", fake_post)
    at.selectbox[0].select(CUSTOM).run()
    at.text_input[0].input("HDFC Bank")
    at.text_input[1].input("hdfcbank.ns").run()
    _start_button(at).click().run()

    assert sent["company_name"] == "HDFC Bank"
    assert sent["ticker"] == "HDFCBANK.NS"
    assert any("planner returned no plan" in e.value for e in at.error)
    assert not _start_button(at).disabled  # re-enabled after the run finished


# ---------- _coerce_to_dict: MCP payload normalisation ----------

def _coerce():
    # Compile just this function: importing app.py would render the whole page.
    import ast, json
    source = Path(APP).read_text()
    fn = next(n for n in ast.parse(source).body
              if isinstance(n, ast.FunctionDef) and n.name == "_coerce_to_dict")
    ns = {"json": json}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), APP, "exec"), ns)
    return ns["_coerce_to_dict"]


@pytest.mark.parametrize("payload, expected", [
    ({"price": 1.0}, {"price": 1.0}),                                     # plain dict
    ('{"price": 1.0}', {"price": 1.0}),                                   # JSON string
    ("not json", {"value": "not json"}),                                  # plain text
    ("[1, 2]", {"value": "[1, 2]"}),                                      # JSON, but not an object
    ([{"type": "text", "text": '{"price": 1.0}'}, {"pe": 30}], {"price": 1.0, "pe": 30}),  # content parts
    (None, {}),
    (42, {}),
])
def test_coerce_to_dict(payload, expected):
    assert _coerce()(payload) == expected
