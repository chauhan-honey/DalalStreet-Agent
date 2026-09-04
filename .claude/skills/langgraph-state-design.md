# Skill: LangGraph State Design

> Production rules for `AgentState`, safe concurrent state aggregation, conditional edges, and cycle safety in the DalalStreet-Agent graph.

---

## 1. Why this matters

LangGraph merges the outputs of concurrently-running nodes into one shared `TypedDict`. If two parallel nodes both write the same key **without a declared reducer**, one silently overwrites the other. In this project `rag_worker` and `market_worker` run in parallel and both feed `critic`, so state reduction is not optional — it is the correctness backbone.

---

## 2. The immutable `AgentState` contract

- Use `TypedDict` (from `typing_extensions` for full `Annotated` support on 3.10).
- Treat the state as **append/merge-only** for concurrent keys. Nodes return **partial** dicts; they never mutate and return the whole state.
- Every key written by ≥2 concurrent nodes MUST carry a reducer via `Annotated`.

```python
from typing import Annotated, Any, Dict, List, Optional
from typing_extensions import TypedDict
import operator


class AgentState(TypedDict):
    company_name: str
    ticker: str                                             # "TCS.NS", "RELIANCE.NS"
    user_query: str
    plan: List[str]
    rag_context: Annotated[List[Dict[str, Any]], operator.add]   # concurrent-safe append
    market_data: Annotated[Dict[str, Any], operator.or_]         # concurrent-safe merge
    audit_findings: List[Dict[str, Any]]
    critic_verdict: str                                     # "PASS" | "NEEDS_RETRY" | "FAIL"
    critic_feedback: Optional[str]
    iteration_count: int
    final_report: Optional[str]
```

### Reducer selection rules
| State shape | Reducer | Reason |
| --- | --- | --- |
| list accumulated by fan-out (`rag_context`) | `operator.add` | concatenates lists from each branch |
| dict merged from independent producers (`market_data`) | `operator.or_` | `{**a, **b}` semantics; keys must not collide |
| scalar owned by exactly one node (`critic_verdict`, `iteration_count`) | **no reducer** | single writer; last-write is correct |

> Anti-pattern: putting a reducer on a single-writer scalar, or omitting one on a fan-in key. Both cause subtle bugs.

---

## 3. Nodes return partial state only

```python
async def rag_worker_node(state: AgentState) -> dict:
    plan = json.loads(state["plan"][0])
    results: List[Dict[str, Any]] = []
    for q in plan.get("rag_queries", []):
        results.extend(vector_store.query(q, state["ticker"], top_k=2))
    return {"rag_context": results}     # only the owned key; reducer appends it
```

Never `return state` or `state["rag_context"] = ...; return state` — that bypasses the reducer and clobbers the parallel branch.

---

## 4. Fan-out / fan-in topology

```python
workflow.add_edge(START, "planner")
workflow.add_edge("planner", "rag_worker")      # parallel branch A
workflow.add_edge("planner", "market_worker")   # parallel branch B
workflow.add_edge("rag_worker", "critic")       # fan-in
workflow.add_edge("market_worker", "critic")    # fan-in
```

LangGraph waits for **both** predecessors before executing `critic`, then applies the reducers. This is why `rag_context` (add) and `market_data` (or_) must be reducer-annotated.

---

## 5. Conditional edges & cycle safety

The router must return a `Literal` and must always have a terminating branch. The loop is bounded by a numeric guard, not by the LLM's mood.

```python
from typing import Literal

def router_condition(state: AgentState) -> Literal["rag_worker", "synthesizer"]:
    if state.get("critic_verdict") == "NEEDS_RETRY" and state.get("iteration_count", 0) < 2:
        return "rag_worker"
    return "synthesizer"

workflow.add_conditional_edges(
    "critic",
    router_condition,
    {"rag_worker": "rag_worker", "synthesizer": "synthesizer"},
)
```

And the bound is also enforced *inside* the critic, so even a misbehaving LLM cannot loop forever:

```python
new_count = state.get("iteration_count", 0) + 1
verdict = data.get("verdict", "PASS")
if new_count >= 2:
    verdict = "PASS"          # hard cap: force exit
return {"critic_verdict": verdict, "iteration_count": new_count, ...}
```

### Cycle-safety checklist
- [ ] Every loop-back edge is guarded by a numeric counter.
- [ ] The counter is incremented exactly once per cycle, inside a single-writer node.
- [ ] The router has a default terminating branch.
- [ ] Worst case (always `NEEDS_RETRY`) terminates in ≤2 iterations.

---

## 6. Fallback routing

Always default to progress, never to a dead end:
- Missing/invalid `critic_verdict` → treat as `PASS` (route to `synthesizer`).
- `iteration_count` unset → `state.get("iteration_count", 0)`.
- Never route to a node that has no path to `END`.

---

## 7. Anti-patterns

- Returning the full mutated state from a node.
- Fan-in key without a reducer (silent overwrite).
- Reducer on a single-writer scalar.
- Unbounded or guard-less conditional loop.
- `KeyError` from assuming a key exists — always `state.get(key, default)` for optional/accumulating keys.
