import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "research"))

import numpy as np
import pandas as pd

from confluence import normalize_bars, summarize_trades, run_one_strategy, add_daily_regime


def make_bars(n=40):
    idx = pd.date_range("2026-01-05 09:30", periods=n, freq="5min", tz="America/New_York")
    close = np.linspace(100, 101, n)
    return pd.DataFrame({
        "Open": close - 0.02, "High": close + 0.05, "Low": close - 0.05,
        "Close": close, "Volume": np.full(n, 10000),
    }, index=idx)


def test_normalize_regular_session_and_order():
    d = normalize_bars(make_bars())
    assert d.index.is_monotonic_increasing
    assert ((d.index.hour * 60 + d.index.minute) >= 570).all()
    assert ((d.index.hour * 60 + d.index.minute) <= 955).all()


def test_summary_metrics():
    trades = [
        {"net_bps": 10.0, "exit_reason": "target"},
        {"net_bps": -5.0, "exit_reason": "stop"},
        {"net_bps": -3.0, "exit_reason": "stop"},
        {"net_bps": 8.0, "exit_reason": "target"},
    ]
    out = summarize_trades(trades)
    assert out["trades"] == 4
    assert out["win_rate_pct"] == 50.0
    assert out["expectancy_bps"] == 2.5
    assert out["max_loss_streak"] == 2


def test_entry_is_next_bar_open():
    d = make_bars(40).rename(columns=str.lower)
    d["atr"] = 0.10
    signal = pd.Series(False, index=d.index)
    signal.iloc[10] = True
    d.iloc[11, d.columns.get_loc("open")] = 100.40
    d.iloc[11, d.columns.get_loc("high")] = 101.00
    d.iloc[11, d.columns.get_loc("low")] = 100.35
    d.iloc[11, d.columns.get_loc("close")] = 100.90
    trades = run_one_strategy(d, "SPY", signal, cost_bps=0)
    assert len(trades) == 1
    assert trades[0]["entry_time"] == d.index[11].isoformat()
    assert trades[0]["entry"] == 100.4


def test_stop_first_when_both_touched_same_bar():
    d = make_bars(40).rename(columns=str.lower)
    d["atr"] = 0.10
    signal = pd.Series(False, index=d.index)
    signal.iloc[10] = True
    d.iloc[11, d.columns.get_loc("open")] = 100.40
    d.iloc[11, d.columns.get_loc("low")] = 99.00
    d.iloc[11, d.columns.get_loc("high")] = 101.20
    trades = run_one_strategy(d, "SPY", signal, cost_bps=0)
    assert trades[0]["exit_reason"] == "stop"


def test_regime_uses_prior_completed_daily_bar():
    day_idx = pd.date_range("2025-10-01", periods=60, freq="B")
    closes = np.linspace(90.0, 110.0, len(day_idx))
    closes[-1] = 80.0
    daily = pd.DataFrame({"Close": closes}, index=day_idx)
    intraday_idx = pd.DatetimeIndex([
        day_idx[-1].tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30),
        (day_idx[-1] + pd.offsets.BDay(1)).tz_localize("America/New_York") + pd.Timedelta(hours=9, minutes=30),
    ])
    intra = pd.DataFrame({"close": [80.0, 80.0]}, index=intraday_idx)
    out = add_daily_regime(intra, daily)
    assert bool(out.iloc[0]["bull_regime"]) is True
    assert bool(out.iloc[1]["bull_regime"]) is False
