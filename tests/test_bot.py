import pandas as pd
from bot import summarize, backtest


def test_summary_empty():
    assert summarize([])["trades"] == 0


def test_summary_profit_factor():
    trades = [{"net_return_pct": 2.0}, {"net_return_pct": -1.0}, {"net_return_pct": 1.0}]
    result = summarize(trades)
    assert result["trades"] == 3
    assert result["profit_factor"] == 3.0


def test_backtest_handles_empty_data():
    df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    assert backtest(df, "TEST") == []
