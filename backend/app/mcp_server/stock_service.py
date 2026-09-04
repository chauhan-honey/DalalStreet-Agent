"""
MARKET-DATA TOOL SERVER  ──  backend/app/mcp_server/stock_service.py
====================================================================

WHAT THIS FILE IS
    A tiny standalone program that exposes two "tools" for fetching live Indian
    stock data (from Yahoo Finance via the `yfinance` library). It is the ONLY
    place in the project allowed to call yfinance directly.

WHAT IS "MCP" AND "FastMCP"
    MCP = Model Context Protocol: an open standard for exposing tools/functions to
    an AI agent over a well-defined channel. `FastMCP` is a small library that
    turns a normal Python function into an MCP tool just by decorating it with
    `@mcp.tool()`. The function's name, type hints, and docstring become the
    tool's public contract that the agent can discover and call.

HOW IT RUNS AND WHO CALLS IT (the flow)
    This file is NOT imported by the graph. Instead graph.py's `market_worker_node`
    launches it as a SEPARATE process:
        python backend/app/mcp_server/stock_service.py
    The two programs then talk over "stdio" (standard input/output) using the MCP
    protocol. graph.py sends a request like {"ticker": "TCS.NS"}, this server runs
    the matching tool, and sends the result dictionary back.

    Diagram:
        graph.py (market_worker_node)  --launches & calls-->  THIS FILE  --> yfinance --> Yahoo Finance

WHY A SEPARATE PROCESS
    Keeping market data behind an MCP tool boundary means the data source can be
    swapped or tested independently, and the agent code never touches yfinance
    directly. Because we speak the protocol over stdout, this file must NEVER
    print anything to stdout except protocol messages (a stray print corrupts it).
"""
from typing import Any, Dict

import yfinance as yf
from fastmcp import FastMCP

# Create the MCP server object. The name is just a label shown to clients.
# Every function decorated with @mcp.tool() below is registered on this server.
mcp = FastMCP("DalalStreet-Market-Server")


def _safe_info(ticker: str) -> Dict[str, Any]:
    """Fetch Yahoo Finance's raw `.info` dictionary for a ticker, defensively.

    yfinance reaches out to the network and can fail in several ways: an unknown
    or delisted ticker, a rate-limit, or a transient connection error. Any of
    those would normally raise an exception. Because this runs behind the MCP
    boundary, we must NOT let an exception escape (it would break the protocol
    channel). So on any problem we simply return an empty dict `{}`, and the
    calling tools below fall back to None for each field.

    Args:
        ticker: Yahoo symbol, e.g. "TCS.NS" (NSE) or "RELIANCE.BO" (BSE).
    Returns:
        The raw info dict from yfinance, or {} if the lookup failed.
    """
    try:
        # yf.Ticker(...).info performs the network call. It may return None for a
        # bad symbol, so `or {}` normalises that to an empty dict.
        return yf.Ticker(ticker).info or {}
    except Exception:
        # Swallow ALL errors on purpose: a data gap must not crash the tool server.
        return {}


@mcp.tool()
def get_live_stock_quote(ticker: str) -> Dict[str, Any]:
    """MCP TOOL #1 — real-time price snapshot for an Indian equity.

    The docstring's first line is what the agent sees as the tool description.
    Called by graph.py's market_worker_node. Returns price, market cap, the
    52-week trading range, and P/E ratios. Missing fields come back as None so
    the critic node can mark them "unsupported" rather than inventing a value.
    """
    # One safe network lookup, then hand-pick only the fields we present.
    info = _safe_info(ticker)
    return {
        "symbol": ticker,
        # Some tickers expose "currentPrice", others "regularMarketPrice"; try both.
        "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "currency": info.get("currency", "INR"),
        "market_cap": info.get("marketCap"),
        "52w_high": info.get("fiftyTwoWeekHigh"),
        "52w_low": info.get("fiftyTwoWeekLow"),
        "trailing_pe": info.get("trailingPE"),   # price / past 12-month earnings
        "forward_pe": info.get("forwardPE"),     # price / forecast earnings
    }


@mcp.tool()
def get_key_financial_ratios(ticker: str) -> Dict[str, Any]:
    """MCP TOOL #2 — fundamental health ratios for an Indian equity.

    Also called by market_worker_node. These are the NUMBERS the critic node
    reconciles against the company's own words in the annual report (e.g. does a
    claim of "expanding margins" match the actual operating_margins figure?).
    """
    # Same defensive pattern: one safe lookup, then select the fundamentals.
    info = _safe_info(ticker)
    return {
        "symbol": ticker,
        "operating_margins": info.get("operatingMargins"),   # profit from core operations
        "profit_margins": info.get("profitMargins"),         # bottom-line profit %
        "return_on_equity": info.get("returnOnEquity"),      # profit vs shareholder equity
        "debt_to_equity": info.get("debtToEquity"),          # leverage / borrowing level
        "free_cashflow": info.get("freeCashflow"),           # cash left after spending
        "revenue_growth": info.get("revenueGrowth"),         # sales growth rate
        "ebitda": info.get("ebitda"),                        # earnings before interest/tax/deprec.
    }


if __name__ == "__main__":
    # Entry point when this file is run as its own process (which is how
    # market_worker_node starts it). transport="stdio" means the server reads
    # requests from stdin and writes responses to stdout using the MCP protocol.
    # Reminder: never print() to stdout elsewhere in this file — it is the channel.
    mcp.run(transport="stdio")
