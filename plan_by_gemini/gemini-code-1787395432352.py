from typing import Annotated, List, Dict, Any, Optional
from typing_extensions import TypedDict
import operator

class AgentState(TypedDict):
    company_name: str
    ticker: str                       # e.g., "TCS.NS", "RELIANCE.NS", "INFY.NS"
    user_query: str
    plan: List[str]                   # Query decomposition output
    rag_context: Annotated[List[Dict[str, Any]], operator.add]
    market_data: Annotated[Dict[str, Any], operator.or_]
    audit_findings: List[Dict[str, Any]]
    critic_verdict: str               # "PASS" | "NEEDS_RETRY" | "FAIL"
    critic_feedback: Optional[str]
    iteration_count: int
    final_report: Optional[str]