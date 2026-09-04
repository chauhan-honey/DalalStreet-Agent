import operator
from typing import Annotated, get_args, get_type_hints

from backend.app.core.state import AgentState


def test_concurrent_keys_declare_reducers():
    """rag_context and market_data must carry reducers for safe parallel fan-in."""
    hints = get_type_hints(AgentState, include_extras=True)

    rag_meta = get_args(hints["rag_context"])[1:]
    market_meta = get_args(hints["market_data"])[1:]

    assert operator.add in rag_meta
    assert operator.or_ in market_meta


def test_reducer_semantics_match_expected_merge():
    """Simulate the fan-in merge critic depends on."""
    rag_a = [{"content": "a", "page": 1}]
    rag_b = [{"content": "b", "page": 2}]
    assert operator.add(rag_a, rag_b) == [{"content": "a", "page": 1}, {"content": "b", "page": 2}]

    market_a = {"quote": {"symbol": "TCS.NS"}}
    market_b = {"ratios": {"operating_margins": 0.25}}
    merged = operator.or_(market_a, market_b)
    assert merged == {"quote": {"symbol": "TCS.NS"}, "ratios": {"operating_margins": 0.25}}


def test_single_writer_scalars_have_no_reducer():
    hints = get_type_hints(AgentState, include_extras=True)
    # critic_verdict is a plain str, not Annotated with a reducer.
    assert hints["critic_verdict"] is str
