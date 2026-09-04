from fastmcp import FastMCP
import yfinance as yf
from typing import Dict, Any

mcp = FastMCP("DalalStreet-Market-Server")

@mcp.tool()
def get_live_stock_quote(ticker: str) -> Dict[str, Any]:
    """Fetches real-time price, market cap, 52-week range, and P/E ratios for an Indian equity ticker."""
    stock = yf.Ticker(ticker)
    info = stock.info
    return {
        "symbol": ticker,
        "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "currency": info.get("currency", "INR"),
        "market_cap": info.get("marketCap"),
        "52w_high": info.get("fiftyTwoWeekHigh"),
        "52w_low": info.get("fiftyTwoWeekLow"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE")
    }

@mcp.tool()
def get_key_financial_ratios(ticker: str) -> Dict[str, Any]:
    """Fetches profitability, operating margins, leverage, and cash flow fundamentals."""
    stock = yf.Ticker(ticker)
    info = stock.info
    return {
        "symbol": ticker,
        "operating_margins": info.get("operatingMargins"),
        "profit_margins": info.get("profitMargins"),
        "return_on_equity": info.get("returnOnEquity"),
        "debt_to_equity": info.get("debtToEquity"),
        "free_cashflow": info.get("freeCashflow"),
        "revenue_growth": info.get("revenueGrowth"),
        "ebitda": info.get("ebitda")
    }

if __name__ == "__main__":
    mcp.run(transport="stdio")