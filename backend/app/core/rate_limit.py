"""
RATE LIMITING  ──  backend/app/core/rate_limit.py
==================================================

WHY THIS EXISTS
    /research/stream is gated by an API key (require_api_key in endpoints.py),
    but a key is just a shared secret — if it ever leaked (pasted somewhere
    public, visible in a screen-share, etc.), nothing would otherwise stop
    someone from scripting repeated calls against it, each one burning real
    Gemini API quota and Azure Container Apps compute. A generous per-IP rate
    limit is cheap insurance against exactly that scenario, on top of (not
    instead of) the API key.

WHAT IS slowapi
    A small rate-limiting library for FastAPI/Starlette, keyed here by the
    caller's IP address. One shared `limiter` instance is created below; it
    gets wired into the FastAPI app in main.py and applied to the specific
    expensive route in endpoints.py via @limiter.limit(...).

WHY IN-MEMORY IS FINE HERE
    slowapi's default backend tracks counts in local process memory, which
    would be wrong for a multi-instance deployment (each instance would count
    separately). This app runs with --max-replicas 1 (see ci-cd.yml), so
    there is only ever one instance — in-memory state is the whole state.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
