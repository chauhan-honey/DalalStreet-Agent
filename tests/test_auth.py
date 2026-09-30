"""Tests for the require_api_key dependency (backend/app/api/v1/endpoints.py).

Deliberately a unit test of the dependency function itself, not a full HTTP
call against /research/stream — that route runs the real multi-agent graph
(live Gemini + yfinance calls), which has no place in a fast test suite.
The auth check is the security-critical logic; this is what actually needs
coverage, and testing it in isolation gets that without the network calls.
"""
import pytest
from fastapi import HTTPException

from backend.app.api.v1 import endpoints
from backend.app.core.config import settings


def test_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(settings, "BACKEND_API_KEY", "correct-key")
    with pytest.raises(HTTPException) as exc_info:
        endpoints.require_api_key(x_api_key=None)
    assert exc_info.value.status_code == 401


def test_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr(settings, "BACKEND_API_KEY", "correct-key")
    with pytest.raises(HTTPException) as exc_info:
        endpoints.require_api_key(x_api_key="wrong-key")
    assert exc_info.value.status_code == 401


def test_accepts_correct_key(monkeypatch):
    monkeypatch.setattr(settings, "BACKEND_API_KEY", "correct-key")
    endpoints.require_api_key(x_api_key="correct-key")  # should not raise


def test_skipped_when_unconfigured(monkeypatch):
    # Local-dev convenience: no BACKEND_API_KEY set at all means the check is
    # a no-op, even with no header — see the dependency's own docstring.
    monkeypatch.setattr(settings, "BACKEND_API_KEY", "")
    endpoints.require_api_key(x_api_key=None)  # should not raise
