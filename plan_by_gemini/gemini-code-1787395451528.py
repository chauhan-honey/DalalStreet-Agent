from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import json
import asyncio
from backend.app.graph import compiled_graph

router = APIRouter()

class ResearchRequest(BaseModel):
    company_name: str
    ticker: str
    user_query: str

@router.post("/research/stream")
async def stream_financial_audit(req: ResearchRequest):
    async def event_generator():
        initial_state = {
            "company_name": req.company_name,
            "ticker": req.ticker,
            "user_query": req.user_query,
            "rag_context": [],
            "market_data": {},
            "audit_findings": [],
            "iteration_count": 0
        }
        
        async for event in compiled_graph.astream(initial_state):
            for node_name, node_output in event.items():
                payload = {
                    "node": node_name,
                    "data": node_output
                }
                yield f"data: {json.dumps(payload, default=str)}\n\n"
                await asyncio.sleep(0.05)

    return StreamingResponse(event_generator(), media_type="text/event-stream")