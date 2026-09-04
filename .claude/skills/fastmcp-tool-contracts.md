# Skill: FastMCP Tool Contracts

> Standards for defining Model Context Protocol tools with FastMCP, `stdio` transport conventions, typed return payloads, and `yfinance` error handling for the market data server.

---

## 1. Why MCP (and not a direct call)

The Architect's invariant #2: **all market data flows through an MCP server**, never a direct `yfinance` import inside a graph node. This keeps the tool layer swappable, independently testable, and protocol-compliant. The server is a standalone process the graph talks to over `stdio`.

---

## 2. Server definition

```python
from typing import Any, Dict
from fastmcp import FastMCP
import yfinance as yf

mcp = FastMCP("DalalStreet-Market-Server")
```

### Tool definition rules
- Decorate with `@mcp.tool()`.
- **Fully typed** signature and return (`(ticker: str) -> Dict[str, Any]`).
- **Docstring is the contract** — the first line is surfaced to the LLM/agent as the tool description; describe exactly what the payload contains.
- Tools are **pure functions of their inputs** with no hidden global state.
- **Never raise across the transport** for expected data gaps — return a structured payload with `None`s instead.

```python
@mcp.tool()
def get_live_stock_quote(ticker: str) -> Dict[str, Any]:
    """Real-time price, market cap, 52-week range, and P/E ratios for an Indian equity ticker."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        info = {}
    return {
        "symbol": ticker,
        "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "currency": info.get("currency", "INR"),
        "market_cap": info.get("marketCap"),
        "52w_high": info.get("fiftyTwoWeekHigh"),
        "52w_low": info.get("fiftyTwoWeekLow"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
    }


@mcp.tool()
def get_key_financial_ratios(ticker: str) -> Dict[str, Any]:
    """Profitability, operating margins, leverage, and cash-flow fundamentals for a ticker."""
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        info = {}
    return {
        "symbol": ticker,
        "operating_margins": info.get("operatingMargins"),
        "profit_margins": info.get("profitMargins"),
        "return_on_equity": info.get("returnOnEquity"),
        "debt_to_equity": info.get("debtToEquity"),
        "free_cashflow": info.get("freeCashflow"),
        "revenue_growth": info.get("revenueGrowth"),
        "ebitda": info.get("ebitda"),
    }
```

---

## 3. stdio transport convention

```python
if __name__ == "__main__":
    mcp.run(transport="stdio")     # the ONLY transport for this project
```

- The server is launched as a subprocess by the client; it speaks JSON-RPC over stdin/stdout.
- **Do not** print to stdout anywhere else in this process — stray prints corrupt the protocol stream. Use `stderr`/logging for diagnostics.
- The script path comes from `settings.MCP_SERVER_SCRIPT`; resolve it to an absolute path before launching.

---

## 4. Client usage (from the graph node)

```python
import os
from langchain_mcp_adapters.client import MultiServerMCPClient

async def market_worker_node(state):
    server_path = os.path.abspath(settings.MCP_SERVER_SCRIPT)
    async with MultiServerMCPClient({
        "dalalstreet_mcp": {"transport": "stdio", "command": "python", "args": [server_path]}
    }) as client:
        tools = await client.get_tools()
        quote_tool = next(t for t in tools if t.name == "get_live_stock_quote")
        ratio_tool = next(t for t in tools if t.name == "get_key_financial_ratios")
        quote = await quote_tool.ainvoke({"ticker": state["ticker"]})
        ratios = await ratio_tool.ainvoke({"ticker": state["ticker"]})
    return {"market_data": {"quote": quote, "ratios": ratios}}
```

- Resolve tools **by name**, not by list index.
- Use the client as an **async context manager** so the subprocess is cleaned up.
- Invoke with `await tool.ainvoke({...})`.

---

## 5. yfinance error handling

yfinance is flaky: it rate-limits, returns partial `.info`, and raises on delisted/unknown tickers.

| Failure | Handling |
| --- | --- |
| Unknown/delisted ticker | `.info` empty → payload of `None`s, no raise |
| Missing individual metric | `.get(key)` → `None`; downstream critic marks `unsupported` |
| Rate limit / transient network | wrap in `try/except`; optionally bounded retry with backoff; never infinite retry |
| `None` `.info` | `info or {}` guard before `.get` |

---

## 6. Anti-patterns

- Importing `yfinance` inside `graph.py` (bypasses MCP).
- Tools that raise raw exceptions across the transport.
- Untyped return values or missing docstrings.
- `print()` to stdout in the server process.
- Resolving tools by positional index.
- Unbounded retry loops on rate-limit.

---

## 7. Validation

1. `python backend/app/mcp_server/stock_service.py` starts and holds a stdio session.
2. From the node, both tools resolve by name and return typed dicts.
3. `ZZZZ.NS` (bogus) returns a `None`-filled payload, no exception.
4. No stray stdout output corrupts the JSON-RPC stream.
