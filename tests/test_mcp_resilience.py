from backend.app.mcp_server import stock_service


def test_safe_info_returns_empty_on_failure(monkeypatch):
    """An unknown/delisted ticker or network error must not raise across the tool boundary."""
    class _Boom:
        def __init__(self, ticker):
            raise RuntimeError("network down")

    monkeypatch.setattr(stock_service.yf, "Ticker", _Boom)
    assert stock_service._safe_info("ZZZZ.NS") == {}


def test_safe_info_handles_none_info(monkeypatch):
    class _NoneInfo:
        def __init__(self, ticker):
            self.info = None

    monkeypatch.setattr(stock_service.yf, "Ticker", _NoneInfo)
    assert stock_service._safe_info("TCS.NS") == {}


def test_safe_info_passes_through_valid_info(monkeypatch):
    class _Good:
        def __init__(self, ticker):
            self.info = {"currentPrice": 4000, "operatingMargins": 0.25}

    monkeypatch.setattr(stock_service.yf, "Ticker", _Good)
    info = stock_service._safe_info("TCS.NS")
    assert info["currentPrice"] == 4000
    assert info["operatingMargins"] == 0.25
