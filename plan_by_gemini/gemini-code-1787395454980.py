from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from backend.app.api.v1.endpoints import router as api_router

app = FastAPI(
    title="DalalStreet-Agent API",
    version="1.0.0",
    description="Multi-Agent Indian Financial Research & Annual Report Auditor"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")