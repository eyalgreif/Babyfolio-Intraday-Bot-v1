"""Confluence-based intraday research engine. Research only; never submits orders."""
from __future__ import annotations
import argparse, json, os
from datetime import date
from pathlib import Path
import numpy as np
import pandas as pd

from data_provider import download_alpaca_symbol, load_csv_symbol

NY = "America/New_York"
DEFAULT_TICKERS = ["SPY", "QQQ", "IWM"]
COST_SCENARIOS_BPS = {"base": 2.0, "stress": 5.0, "severe": 8.0}

def _flatten_columns(df):
    out = df.copy()
    if isinstance(out.columns, pd.MultiIndex):
        out.columns = out.columns.get_level_values(0)
    out.columns = [str(c).strip().lower() for c in out.columns]
    return out

def normalize_bars(df):
    d = _flatten_columns(df)
    required = ["open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in d.columns]
    if missing:
        raise ValueError(f"Missing OHLCV columns: {missing}")
    d = d[required].copy().dropna()
    idx = pd.to_datetime(d.index)
    idx = idx.tz_localize(NY) if idx.tz is None else idx.tz_convert(NY)
    d.index = idx
    d = d[~d.index.duplicated(keep="last")].sort_index()
    mins = d.index.hour * 60 + d.index.minute
    d = d[(mins >= 570) & (mins <= 955)]
    valid = ((d["high"] >= d[["open", "close", "low"]].max(axis=1))
             & (d["low"] <= d[["open", "close", "high"]].min(axis=1))
             & (d["volume"] >= 0) & (d["open"] > 0))
    return d.loc[valid].copy()

def add_daily_regime(intraday, daily):
    """Use only the prior completed daily close for the regime filter."""
    d = intraday.copy()
    day = _flatten_columns(daily)
    if "close" not in day:
        raise ValueError("Daily data must contain Close")
    day = day[["close"]].dropna().copy()
    idx = pd.to_datetime(day.index)
    if idx.tz is not None:
        idx = idx.tz_convert(NY).tz_localize(None)
    day.index = idx.normalize()
    day = day[~day.index.duplicated(keep="last")].sort_index()
    day["ema20"] = day["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    day["ema50"] = day["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    day["bull_regime"] = ((day["close"] > day["ema20"]) & (day["ema20"] > day["ema50"])).shift(1)
    dates = pd.DatetimeIndex(d.index.date)
    regime = day["bull_regime"].to_dict()
    d["session_date"] = dates
    d["bull_regime"] = [regime.get(pd.Timestamp(x), False) for x in dates]
    d["bull_regime"] = d["bull_regime"].fillna(False).astype(bool)
    return d

def add_features(intraday, daily):
    d = normalize_bars(intraday)
    d = add_daily_regime(d, daily)
    d["session_date"] = pd.Index(d.index.date)
    d["bar_slot"] = d.index.hour * 12 + d.index.minute // 5
    typical = (d["high"] + d["low"] + d["close"]) / 3.0
    pv = typical * d["volume"]
    d["vwap"] = (pv.groupby(d["session_date"]).cumsum()
                 / d["volume"].groupby(d["session_date"]).cumsum().replace(0, np.nan))
    prev_close = d.groupby("session_date")["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - prev_close).abs(),
                    (d["low"] - prev_close).abs()], axis=1).max(axis=1)
    d["atr"] = tr.groupby(d["session_date"]).transform(lambda s: s.rolling(14, min_periods=10).mean())
    # Reset short-horizon oscillators/EMAs each regular session, not across overnight gaps.
    delta = d.groupby("session_date")["close"].diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    avg_gain = gain.groupby(d["session_date"]).transform(
        lambda s: s.ewm(alpha=0.5, adjust=False, min_periods=2).mean())
    avg_loss = loss.groupby(d["session_date"]).transform(
        lambda s: s.ewm(alpha=0.5, adjust=False, min_periods=2).mean())
    rs = avg_gain / avg_loss.replace(0, np.nan)
    d["rsi2"] = 100 - 100 / (1 + rs)
    d.loc[(avg_loss == 0) & (avg_gain > 0), "rsi2"] = 100
    d["ema8"] = d.groupby("session_date")["close"].transform(lambda s: s.ewm(span=8, adjust=False).mean())
    d["ema21"] = d.groupby("session_date")["close"].transform(lambda s: s.ewm(span=21, adjust=False).mean())
    candle_range = (d["high"] - d["low"]).replace(0, np.nan)
    d["close_location"] = (d["close"] - d["low"]) / candle_range
    # Relative volume vs. same 5-minute time slot in the previous 20 sessions.
    d["vol_median_20"] = d.groupby("bar_slot")["volume"].transform(
        lambda s: s.shift(1).rolling(20, min_periods=8).median())
    d["rvol"] = d["volume"] / d["vol_median_20"].replace(0, np.nan)
    for col in ["vwap", "atr", "close"]:
        d["prior_" + col] = d.groupby("session_date")[col].shift(1)
    return d

def make_signals(d):
    """Pre-specified hypotheses; no fitting/tuning is performed here."""
    stretched = d["prior_close"] < (d["prior_vwap"] - 0.5 * d["prior_atr"])
    reclaim = (d["close"] > d["vwap"]) & (d["prior_close"] <= d["prior_vwap"])
    bullish_close = (d["close"] > d["open"]) & (d["close_location"] >= 0.65)
    base = d["bull_regime"] & stretched & reclaim & bullish_close
    rvol = d["rvol"] >= 1.10
    rsi_recovery = (d["rsi2"].shift(1) <= 35) & (d["rsi2"] > 35)
    trend_pullback = (d["bull_regime"] & (d["ema8"] > d["ema21"])
                      & (d["low"].shift(1) <= d["ema8"].shift(1))
                      & (d["close"] > d["ema8"]) & bullish_close & rvol)
    return {
        "vwap_reclaim_base": base.fillna(False),
        "vwap_reclaim_plus_rvol": (base & rvol).fillna(False),
        "vwap_reclaim_plus_rvol_rsi": (base & rvol & rsi_recovery).fillna(False),
        "trend_pullback_plus_rvol": trend_pullback.fillna(False),
    }

def run_one_strategy(d, ticker, signal, reward_r=0.7, stop_atr_buffer=0.10,
                     max_hold_bars=6, cost_bps=2.0):
    trades, traded_days = [], set()
    idx = d.index
    for i in range(len(d) - 1):
        if not bool(signal.iloc[i]):
            continue
        ts, session = idx[i], idx[i].date()
        minute = ts.hour * 60 + ts.minute
        if minute < 590 or minute > 895 or session in traded_days:
            continue
        entry_i = i + 1
        if idx[entry_i].date() != session:
            continue
        entry = float(d.iloc[entry_i]["open"])
        atr = float(d.iloc[i]["atr"])
        if not np.isfinite(entry) or not np.isfinite(atr) or atr <= 0:
            continue
        stop = float(d.iloc[i]["low"]) - stop_atr_buffer * atr
        risk = entry - stop
        if risk <= 0 or risk / entry > 0.01:
            continue
        target = entry + reward_r * risk
        exit_price = reason = exit_i = None
        for j in range(entry_i, min(len(d), entry_i + max_hold_bars)):
            row = d.iloc[j]
            if idx[j].date() != session:
                break
            low, high, opn = float(row["low"]), float(row["high"]), float(row["open"])
            if j > entry_i and opn <= stop:
                exit_price, reason, exit_i = opn, "stop_gap", j
                break
            if j > entry_i and opn >= target:
                exit_price, reason, exit_i = opn, "target_gap", j
                break
            # Conservative bar assumption: stop gets priority if both levels are touched.
            if low <= stop:
                exit_price, reason, exit_i = stop, "stop", j
                break
            if high >= target:
                exit_price, reason, exit_i = target, "target", j
                break
            if j == entry_i + max_hold_bars - 1 or (idx[j].hour == 15 and idx[j].minute >= 55):
                exit_price, reason, exit_i = float(row["close"]), "time_exit", j
                break
        if exit_price is None or exit_i is None:
            continue
        gross_bps = (exit_price / entry - 1.0) * 10000
        net_bps = gross_bps - cost_bps
        trades.append({
            "ticker": ticker, "signal_time": ts.isoformat(),
            "entry_time": idx[entry_i].isoformat(), "exit_time": idx[exit_i].isoformat(),
            "entry": round(entry, 4), "exit": round(exit_price, 4),
            "stop": round(stop, 4), "target": round(target, 4),
            "exit_reason": reason, "risk_pct": round(risk / entry * 100, 4),
            "gross_bps": round(gross_bps, 3), "cost_bps": round(cost_bps, 3),
            "net_bps": round(net_bps, 3),
            "net_r": round(net_bps / (risk / entry * 10000), 4),
            "win": bool(net_bps > 0),
        })
        traded_days.add(session)
    return trades

def summarize_trades(trades):
    if not trades:
        return {"trades": 0, "win_rate_pct": None, "expectancy_bps": None,
                "profit_factor": None, "avg_win_bps": None, "avg_loss_bps": None,
                "max_loss_streak": 0, "max_drawdown_bps": None}
    x = pd.DataFrame(trades)
    net = x["net_bps"].astype(float)
    wins, losses = net[net > 0], net[net < 0]
    gp, gl = float(wins.sum()), float(-losses.sum())
    streak = max_streak = 0
    for is_win in net > 0:
        streak = 0 if is_win else streak + 1
        max_streak = max(max_streak, streak)
    equity = net.cumsum()
    dd = equity - equity.cummax().clip(lower=0)
    return {
        "trades": int(len(x)), "win_rate_pct": round(float((net > 0).mean() * 100), 2),
        "expectancy_bps": round(float(net.mean()), 3),
        "profit_factor": round(gp / gl, 3) if gl > 0 else None,
        "avg_win_bps": round(float(wins.mean()), 3) if len(wins) else None,
        "avg_loss_bps": round(float(losses.mean()), 3) if len(losses) else None,
        "max_loss_streak": int(max_streak), "max_drawdown_bps": round(float(dd.min()), 3),
        "target_exit_pct": round(float(x["exit_reason"].isin(["target", "target_gap"]).mean() * 100), 2),
        "stop_exit_pct": round(float(x["exit_reason"].isin(["stop", "stop_gap"]).mean() * 100), 2),
    }

def download_data(ticker, provider="auto", intraday_period="60d",
                  start_date="2016-01-01", end_date=None, feed="sip",
                  data_dir="data/market"):
    end_date = end_date or date.today().isoformat()
    alpaca_key = os.getenv("APCA_API_KEY_ID")
    alpaca_secret = os.getenv("APCA_API_SECRET_KEY")
    selected = provider
    if selected == "auto":
        selected = "alpaca" if alpaca_key and alpaca_secret else "yahoo"
    if selected == "alpaca":
        intra, daily, metadata = download_alpaca_symbol(
            ticker, start=start_date, end=end_date, feed=feed
        )
        return normalize_bars(intra), _flatten_columns(daily), metadata
    if selected == "csv":
        intra, daily, metadata = load_csv_symbol(ticker, data_dir=data_dir)
        return normalize_bars(intra), _flatten_columns(daily), metadata
    if selected != "yahoo":
        raise ValueError("provider must be auto, alpaca, yahoo, or csv")
    import yfinance as yf
    intra = yf.download(ticker, period=intraday_period, interval="5m", auto_adjust=False,
                        progress=False, threads=False)
    daily = yf.download(ticker, period="2y", interval="1d", auto_adjust=False,
                        progress=False, threads=False)
    if intra.empty or daily.empty:
        raise RuntimeError(f"No data returned for {ticker} (intraday={len(intra)}, daily={len(daily)})")
    intra = normalize_bars(intra)
    daily = _flatten_columns(daily)
    metadata = {
        "provider": "yahoo", "feed": "Yahoo Finance",
        "start_requested": intraday_period, "end_requested": end_date,
        "intraday_bars": int(len(intra)), "daily_bars": int(len(daily)),
        "intraday_first": intra.index.min().isoformat() if len(intra) else None,
        "intraday_last": intra.index.max().isoformat() if len(intra) else None,
        "daily_first": str(daily.index.min()) if len(daily) else None,
        "daily_last": str(daily.index.max()) if len(daily) else None,
    }
    return intra, daily, metadata

def run_research(tickers, intraday_period="60d", provider="auto",
                 start_date="2016-01-01", end_date=None, feed="sip",
                 data_dir="data/market"):
    results, trades_by_key, errors, data_metadata = [], {}, {}, {}
    period_results = []
    end_date = end_date or date.today().isoformat()
    splits = {
        "development_2016_2021": ("2016-01-01", "2021-12-31"),
        "validation_2022_2024": ("2022-01-01", "2024-12-31"),
        "historical_holdout_2025_to_2026-08-26": ("2025-01-01", "2026-08-26"),
        "recent_pilot_already_inspected": ("2026-08-27", "2099-12-31"),
    }
    for ticker in tickers:
        try:
            intra, daily, metadata = download_data(
                ticker, provider=provider, intraday_period=intraday_period,
                start_date=start_date, end_date=end_date, feed=feed, data_dir=data_dir
            )
            data_metadata[ticker] = metadata
            features = add_features(intra, daily)
            for strategy, signal in make_signals(features).items():
                for scenario, cost_bps in COST_SCENARIOS_BPS.items():
                    trades = run_one_strategy(features, ticker, signal, cost_bps=cost_bps)
                    key = f"{ticker}:{strategy}:{scenario}"
                    trades_by_key[key] = trades
                    results.append({"ticker": ticker, "strategy": strategy,
                                    "cost_scenario": scenario, "cost_bps": cost_bps,
                                    **summarize_trades(trades)})
                    for split_name, (split_start, split_end) in splits.items():
                        split_trades = [
                            trade for trade in trades
                            if split_start <= pd.Timestamp(trade["entry_time"]).date().isoformat() <= split_end
                        ]
                        period_results.append({
                            "ticker": ticker, "strategy": strategy,
                            "cost_scenario": scenario, "cost_bps": cost_bps,
                            "period": split_name, **summarize_trades(split_trades)
                        })
        except Exception as exc:
            errors[ticker] = f"{type(exc).__name__}: {exc}"
    providers = sorted({m["provider"] for m in data_metadata.values()})
    return {
        "disclaimer": "Research only. Yahoo 5-minute data is a short exploratory sample. Alpaca IEX is a single-venue feed (~2.5% of US equity volume), not consolidated SIP. No true order-book OFI data. Signals fill next bar open; stop wins if both stop and target are touched.",
        "provider": providers, "intraday_period": intraday_period,
        "start_date": start_date, "end_date": end_date, "feed": feed,
        "tickers": tickers, "data_metadata": data_metadata,
        "cost_scenarios_bps_round_trip": COST_SCENARIOS_BPS,
        "results": results, "period_results": period_results,
        "trades": trades_by_key, "errors": errors,
    }

def write_report(report, out_dir="reports"):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "confluence_results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "confluence_trades.json").write_text(json.dumps(report["trades"], indent=2), encoding="utf-8")
    frame = pd.DataFrame(report["results"])
    periods = pd.DataFrame(report["period_results"])
    if not frame.empty:
        frame.to_csv(out / "confluence_summary.csv", index=False)
    if not periods.empty:
        periods.to_csv(out / "confluence_period_summary.csv", index=False)
    lines = ["# Intraday Confluence Research", "",
             f"Requested range: {report['start_date']} to {report['end_date']}",
             f"Provider(s): {', '.join(report['provider']) or 'none'}",
             f"Feed: {report['feed']}", "",
             f"> {report['disclaimer']}", "", "## Data coverage", ""]
    for ticker, metadata in report["data_metadata"].items():
        lines.append(
            f"- **{ticker}:** {metadata['intraday_bars']:,} intraday bars "
            f"({metadata['intraday_first']} to {metadata['intraday_last']}); "
            f"{metadata['daily_bars']:,} daily bars; provider={metadata['provider']}, feed={metadata['feed']}"
        )
    lines += ["", "## Aggregate strategy results", ""]
    lines.append(frame.to_markdown(index=False) if not frame.empty else "No strategies returned trades.")
    lines += ["", "## Calendar-period diagnostics", "",
              "> The 2026-08-27 onward segment overlaps the initial 60-day pilot already inspected; it is descriptive, not a clean holdout.", ""]
    lines.append(periods.to_markdown(index=False) if not periods.empty else "No period results.")
    if report["errors"]:
        lines += ["", "## Data errors", ""] + [f"- **{k}:** {v}" for k, v in report["errors"].items()]
    lines += ["", "## Interpretation guardrails", "",
              "- Do not select a winner from the recent 60-day pilot.",
              "- The 2016-2021 / 2022-2024 / 2025-2026 splits are chronological diagnostics; strategy rules were not preregistered before this project, so they are not a substitute for a fresh prospective holdout.",
              "- Compare base, stress, and severe cost scenarios.",
              "- No promotion without walk-forward evaluation and a frozen forward paper-trading holdout.",
              "- Relative volume on Alpaca IEX is an IEX-only bar-volume proxy, not consolidated market volume or true order-flow imbalance.",
              "- OHLC bars cannot resolve intrabar ordering; stop-first is the conservative assumption.", ""]
    (out / "confluence_report.md").write_text("\n".join(lines), encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(description="Research-only confluence intraday backtest")
    parser.add_argument("--provider", choices=["auto", "yahoo", "alpaca", "csv"], default="auto")
    parser.add_argument("--period", default="60d", help="Yahoo intraday period")
    parser.add_argument("--start-date", default="2016-01-01", help="Alpaca history start (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="Alpaca history end (YYYY-MM-DD; defaults to today)")
    parser.add_argument("--feed", choices=["iex", "sip"], default=os.getenv("ALPACA_DATA_FEED", "sip"))
    parser.add_argument("--data-dir", default="data/market", help="Directory with SYMBOL_5m.csv and SYMBOL_1d.csv for --provider csv")
    parser.add_argument("--tickers", nargs="*", default=DEFAULT_TICKERS)
    args = parser.parse_args()
    report = run_research(
        args.tickers, args.period, provider=args.provider,
        start_date=args.start_date, end_date=args.end_date, feed=args.feed, data_dir=args.data_dir
    )
    write_report(report)
    for row in report["results"]:
        print(row)
    if report["errors"]:
        print("DATA ERRORS:", report["errors"])
    if not report["results"]:
        raise SystemExit("No results generated")

if __name__ == "__main__":
    main()
