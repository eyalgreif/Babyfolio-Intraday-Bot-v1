"""Research-only intraday strategy backtest. No live order execution."""
from __future__ import annotations
import argparse
from pathlib import Path
import json
import pandas as pd
import yfinance as yf

OUT = Path("reports")
TICKERS = ["AAPL", "MSFT", "NVDA", "AMD", "META", "AMZN", "GOOGL", "TSLA", "SPY"]


def download_data(ticker: str, period: str = "60d", interval: str = "5m") -> pd.DataFrame:
    df = yf.download(ticker, period=period, interval=interval, auto_adjust=True, progress=False)
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.title)
    required = ["Open", "High", "Low", "Close", "Volume"]
    df = df[[c for c in required if c in df.columns]].dropna()
    df.index = pd.to_datetime(df.index)
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["ema8"] = d["Close"].ewm(span=8, adjust=False).mean()
    d["ema21"] = d["Close"].ewm(span=21, adjust=False).mean()
    typical = (d["High"] + d["Low"] + d["Close"]) / 3
    session = pd.Series(d.index.date, index=d.index)
    cum_pv = (typical * d["Volume"]).groupby(session).cumsum()
    cum_vol = d["Volume"].groupby(session).cumsum().replace(0, pd.NA)
    d["vwap"] = cum_pv / cum_vol
    d["atr"] = (d["High"] - d["Low"]).rolling(14).mean()
    return d


def backtest(df: pd.DataFrame, ticker: str, risk_fraction: float = 0.0025) -> list[dict]:
    """Simple long-only VWAP reclaim model; one position at a time, 1.5R target."""
    if len(df) < 30:
        return []
    d = add_indicators(df)
    trades = []
    in_trade = False
    entry = stop = target = 0.0
    entry_time = None
    for i in range(22, len(d)):
        row = d.iloc[i]
        prev = d.iloc[i - 1]
        # Avoid entries on incomplete indicator rows and require an intraday VWAP reclaim.
        if not in_trade:
            if (pd.notna(row["vwap"]) and pd.notna(row["atr"]) and row["Close"] > row["vwap"]
                and prev["Close"] <= prev["vwap"] and row["ema8"] > row["ema21"]):
                candidate_stop = min(float(prev["Low"]), float(row["Low"])) - 0.1 * float(row["atr"])
                risk = float(row["Close"]) - candidate_stop
                if risk <= 0 or not pd.notna(risk):
                    continue
                entry = float(row["Close"])
                stop = candidate_stop
                target = entry + 1.5 * risk
                entry_time = d.index[i]
                in_trade = True
        else:
            exit_price = None
            reason = ""
            # Conservative assumption: if stop and target both trade in one candle, stop wins.
            if float(row["Low"]) <= stop:
                exit_price, reason = stop, "stop"
            elif float(row["High"]) >= target:
                exit_price, reason = target, "target"
            elif d.index[i].date() != d.index[i-1].date() or (d.index[i].hour == 15 and d.index[i].minute >= 50):
                exit_price, reason = float(row["Close"]), "session_end"
            if exit_price is not None:
                # Simple 5 bps round-trip cost estimate.
                gross = (exit_price - entry) / entry
                net = gross - 0.0005
                trades.append({"ticker": ticker, "entry_time": str(entry_time), "exit_time": str(d.index[i]),
                               "entry": round(entry, 4), "exit": round(exit_price, 4), "reason": reason,
                               "gross_return_pct": round(gross * 100, 4), "net_return_pct": round(net * 100, 4)})
                in_trade = False
    return trades


def summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate_pct": 0, "profit_factor": 0, "net_return_sum_pct": 0}
    returns = [t["net_return_pct"] for t in trades]
    wins = sum(x for x in returns if x > 0)
    losses = -sum(x for x in returns if x < 0)
    return {"trades": len(returns), "win_rate_pct": round(100 * sum(x > 0 for x in returns) / len(returns), 2),
            "profit_factor": round(wins / losses, 3) if losses else None,
            "net_return_sum_pct": round(sum(returns), 3)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="60d")
    parser.add_argument("--tickers", nargs="*", default=TICKERS)
    args = parser.parse_args()
    OUT.mkdir(exist_ok=True)
    all_trades = []
    errors = {}
    for ticker in args.tickers:
        try:
            data = download_data(ticker, args.period)
            trades = backtest(data, ticker)
            all_trades.extend(trades)
            print(f"{ticker}: {len(data)} bars, {len(trades)} trades")
        except Exception as exc:
            errors[ticker] = str(exc)
            print(f"{ticker}: ERROR {exc}")
    trades_df = pd.DataFrame(all_trades)
    trades_df.to_csv(OUT / "trades.csv", index=False)
    report = {"warning": "Exploratory backtest only; yfinance intraday history is limited; not evidence of a tradable edge.",
              "data_period": args.period, "tickers": args.tickers, "summary": summarize(all_trades), "errors": errors}
    (OUT / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = ["# Babyfolio Intraday Bot — Research Report", "", f"**Data request:** {args.period}", "",
             "> Exploratory only. Data availability and intraday history are limited. No live trading. Results are not predictive.", "",
             "## Summary", "", f"- Trades: {report['summary']['trades']}",
             f"- Win rate: {report['summary']['win_rate_pct']}%",
             f"- Profit factor: {report['summary']['profit_factor']}",
             f"- Sum of trade returns (not compounded): {report['summary']['net_return_sum_pct']}%", "",
             "## Per-trade results", ""]
    if trades_df.empty:
        lines.append("No trades returned. Check data availability and workflow logs.")
    else:
        lines.append(trades_df.to_markdown(index=False))
    if errors:
        lines += ["", "## Data errors", ""] + [f"- {k}: {v}" for k, v in errors.items()]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
