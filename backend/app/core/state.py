"""
SHARED STATE  ──  backend/app/core/state.py
===========================================

WHAT THIS FILE IS
    The "shared memory" definition for the whole agent workflow. In LangGraph
    (the orchestration framework), the pipeline is a set of steps called "nodes".
    Every node receives ONE dictionary (the state) as input and returns a small
    dictionary of updates as output. `AgentState` below declares exactly which
    keys that shared dictionary may contain and what type each holds.

WHY A TypedDict
    `TypedDict` is just a dictionary whose keys and value-types are known ahead of
    time. It gives editors/type-checkers autocomplete and catches typos, but at
    runtime it behaves like a normal dict (e.g. state["ticker"]).

THE KEY IDEA: REDUCERS (how parallel steps merge safely)
    Two nodes in graph.py run AT THE SAME TIME: `rag_worker` (reads the PDF) and
    `market_worker` (fetches live stock data). Both finish and write into the same
    state. If they wrote to the same key with no rule, one would overwrite the
    other. To prevent that, we mark those keys with `Annotated[type, reducer]`:
        - operator.add on a list  -> the two lists are CONCATENATED (a + b)
        - operator.or_ on a dict   -> the two dicts are MERGED  ({**a, **b})
    LangGraph reads that annotation and applies the reducer automatically when the
    parallel branches "fan in". Keys written by only ONE node need no reducer.

WHO USES THIS FILE
    - graph.py       : every node function is typed `(state: AgentState) -> dict`.
    - endpoints.py   : builds the initial state dict that starts a run.

FIELD-TO-NODE MAP (which step fills which key) — see graph.py
    company_name / ticker / user_query -> provided by the API caller (the inputs)
    plan                               -> written by planner_node
    rag_context                        -> written by rag_worker_node   (reducer: add)
    market_data                        -> written by market_worker_node (reducer: or_)
    audit_findings / critic_verdict /
    critic_feedback / iteration_count  -> written by critic_node
    final_report                       -> written by synthesizer_node
"""
import operator
from typing import Annotated, Any, Dict, List, Optional

from typing_extensions import TypedDict


class AgentState(TypedDict):
    """The single dictionary passed through every node of the LangGraph workflow.

    Each node reads what it needs from this state and returns ONLY the keys it is
    responsible for. LangGraph merges those returned keys back into the state.
    """

    # --- Inputs supplied by the API caller when a run starts (see endpoints.py) ---
    company_name: str                 # display name, e.g. "Tata Consultancy Services"
    ticker: str                       # yfinance symbol, e.g. "TCS.NS", "RELIANCE.NS", "INFY.NS"
    user_query: str                   # the research/audit directive the user typed

    # --- Produced by planner_node ---
    # The planner asks the LLM to break the query into sub-questions. Its JSON
    # answer is stored as a single-element list of raw text, parsed later.
    plan: List[str]

    # --- Concurrent fan-in keys: written by two parallel nodes, so they need reducers ---
    # rag_worker_node returns a list of PDF snippets; operator.add concatenates the
    # lists coming from each retrieval sub-query into one combined list.
    rag_context: Annotated[List[Dict[str, Any]], operator.add]
    # market_worker_node returns {"quote": ..., "ratios": ...}; operator.or_ merges
    # dictionaries so parallel writes combine instead of clobbering each other.
    market_data: Annotated[Dict[str, Any], operator.or_]

    # --- Produced by critic_node (the auditor that reconciles claims vs numbers) ---
    audit_findings: List[Dict[str, Any]]   # per-claim results (aligned / contradiction / unsupported)
    critic_verdict: str                    # "PASS" | "NEEDS_RETRY" | "FAIL" (single writer -> no reducer)
    critic_feedback: Optional[str]         # if NEEDS_RETRY, an instruction for the next retrieval pass
    iteration_count: int                   # how many times the critic has run (used to bound the loop)

    # --- Produced by synthesizer_node (the final writer) ---
    final_report: Optional[str]            # the finished markdown research memo returned to the user
