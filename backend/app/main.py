"""
APPLICATION ENTRY POINT  ──  backend/app/main.py
================================================

WHAT THIS FILE IS
    The file the web server actually runs. You start the backend with:
        uvicorn backend.app.main:app --reload --port 8000
    That command loads `app` (created below) and serves it.

WHAT IT DOES (only three things — kept deliberately thin)
    1. Creates the FastAPI application object.
    2. Enables CORS so the Streamlit frontend (a different origin) may call it.
    3. Mounts the API routes from endpoints.py under the /api/v1 URL prefix.

    All real logic lives elsewhere (routes in endpoints.py, workflow in graph.py).
    Keeping main.py minimal is intentional: it is just the wiring/bootstrap layer.

WHAT IS CORS
    Cross-Origin Resource Sharing. Browsers block a page served from one origin
    (e.g. the Streamlit app on streamlit.app) from calling an API on a different
    origin (this backend, on azurecontainerapps.io) unless the API explicitly
    allows it. The middleware below grants that permission — restricted to the
    real frontend origin(s) in settings.ALLOWED_ORIGINS, not "*" (allow-all).
    "*" plus credentialed requests is actually worse than it sounds: browsers
    forbid literally combining them, so CORS libraries (Starlette's included)
    fall back to reflecting back whatever Origin header the request sent —
    i.e. "trust any origin" once credentials are involved. We don't use
    cookie/session auth at all (just the X-API-Key header, which CORS
    "credentials" doesn't gate), so allow_credentials=False sidesteps that
    whole class of misconfiguration rather than merely narrowing it.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from backend.app.api.v1.endpoints import router as api_router
from backend.app.core.config import settings
from backend.app.core.rate_limit import limiter

# The FastAPI application. title/version/description show up in the auto-generated
# API docs at http://localhost:8000/docs — but only in development; ENVIRONMENT=
# production (set on the deployed container) turns them off, since public docs
# for a live API are a minor info-disclosure surface not worth carrying once
# it's actually running on the internet.
_docs_enabled = settings.ENVIRONMENT != "production"
app = FastAPI(
    title="DalalStreet-Agent API",
    version="1.0.0",
    description="Multi-Agent Indian Financial Research & Annual Report Auditor",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# Wire up slowapi's rate limiter (see core/rate_limit.py for why): the limiter
# lives on app.state so @limiter.limit(...) decorators elsewhere can find it,
# and the exception handler turns a limit breach into a clean 429 instead of
# an unhandled error.
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Allow only the real frontend origin(s) to call this API cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Attach every route defined in endpoints.py. With the prefix, the stream route
# becomes POST /api/v1/research/stream and health becomes GET /api/v1/health.
app.include_router(api_router, prefix="/api/v1")
