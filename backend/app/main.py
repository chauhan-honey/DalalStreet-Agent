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
    (e.g. the Streamlit app on :8501) from calling an API on a different origin
    (:8000) unless the API explicitly allows it. The middleware below grants that
    permission. "*" (allow all) is fine for local development.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.app.api.v1.endpoints import router as api_router

# The FastAPI application. title/version/description show up in the auto-generated
# API docs at http://localhost:8000/docs.
app = FastAPI(
    title="DalalStreet-Agent API",
    version="1.0.0",
    description="Multi-Agent Indian Financial Research & Annual Report Auditor",
)

# Allow the browser-based frontend to call this API. In development we accept any
# origin; in production you would restrict allow_origins to the real frontend URL.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Attach every route defined in endpoints.py. With the prefix, the stream route
# becomes POST /api/v1/research/stream and health becomes GET /api/v1/health.
app.include_router(api_router, prefix="/api/v1")
