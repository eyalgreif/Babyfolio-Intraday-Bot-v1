"""Download IBKR historical bars locally; read-only connection, no order methods used."""
from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


def normalize_ibkr_bars(raw: pd.DataFrame, daily: bool = False) -> pd.DataFrame:
    """Convert util.df(BarDataList) output to the research CSV schema."""
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    frame = raw.copy()
    frame.columns = [str(c).strip().lower() for c in frame.columns]
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"IBKR bars missing fields: {sorted(missing)}")
    if daily:
        timestamps = pd.to_datetime(frame["date"], errors="coerce")
        if getattr(timestamps.dt, "tz", None) is not None:
            timestamps = timestamps.dt.tz_convert("America/New_York").dt.tz_localize(None)
        timestamps = timestamps.dt.normalize()
    else:
        timestamps = pd.to_datetime(frame["date"], utc=True, errors="coerce")
    out = pd.DataFrame({
        "timestamp": timestamps,
        "open": pd.to_numeric(frame["open"], errors="coerce"),
        "high": pd.to_numeric(frame["high"], errors="coerce"),
        "low": pd.to_numeric(frame["low"], errors="coerce"),
        "close": pd.to_numeric(frame["close"], errors="coerce"),
        "volume": pd.to_numeric(frame["volume"], errors="coerce"),
    }).dropna()
    out = out.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    return out


def download_symbol(ib, util, Stock, symbol: str, start_date: date, end_date: date, out_dir: Path):
    contract = Stock(symbol, "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise RuntimeError(f"IBKR could not qualify contract for {symbol}")
    contract = qualified[0]

    chunks = []
    end_cursor = ""
    max_batches = 1500
    for batch in range(max_batches):
        bars = ib.reqHistoricalData(
            contract,
            endDateTime=end_cursor,
            durationStr="10 D",
            barSizeSetting="5 mins",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=2,
            keepUpToDate=False,
            timeout=90,
        )
        if not bars:
            break
        normalized = normalize_ibkr_bars(util.df(bars), daily=False)
        if normalized.empty:
            break
        chunks.append(normalized)
        oldest = normalized["timestamp"].min()
        print(f"{symbol}: batch {batch + 1}, {len(normalized)} bars, oldest={oldest}")
        oldest_ts = pd.Timestamp(oldest)
        if oldest_ts.date() <= start_date:
            break
        # The next request ends just before the first bar in this batch.
        end_cursor = (oldest_ts.to_pydatetime() - timedelta(seconds=1))
        time.sleep(0.35)
    if not chunks:
        raise RuntimeError(f"IBKR returned no 5-minute TRADES bars for {symbol}")

    intraday = pd.concat(chunks, ignore_index=True)
    intraday = intraday.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    intraday = intraday[
        (pd.to_datetime(intraday["timestamp"], utc=True).dt.tz_convert("America/New_York").dt.date >= start_date)
        & (pd.to_datetime(intraday["timestamp"], utc=True).dt.tz_convert("America/New_York").dt.date <= end_date)
    ].copy()

    years = max(1, ((end_date - start_date).days // 365) + 1)
    daily_bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=f"{years} Y",
        barSizeSetting="1 day",
        whatToShow="TRADES",
        useRTH=True,
        formatDate=1,
        keepUpToDate=False,
        timeout=120,
    )
    daily = normalize_ibkr_bars(util.df(daily_bars), daily=True)
    if daily.empty:
        raise RuntimeError(f"IBKR returned no daily TRADES bars for {symbol}")

    out_dir.mkdir(parents=True, exist_ok=True)
    intraday_path = out_dir / f"{symbol}_5m.csv"
    daily_path = out_dir / f"{symbol}_1d.csv"
    intraday.to_csv(intraday_path, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    daily.to_csv(daily_path, index=False, date_format="%Y-%m-%d")
    print(f"{symbol}: saved {len(intraday):,} 5m bars and {len(daily):,} daily bars")
    return {"symbol": symbol, "intraday_bars": len(intraday), "daily_bars": len(daily),
            "intraday_path": str(intraday_path), "daily_path": str(daily_path)}


def main():
    parser = argparse.ArgumentParser(description="Download historical IBKR bars locally in read-only mode.")
    parser.add_argument("--symbols", nargs="*", default=["SPY", "QQQ", "IWM"])
    parser.add_argument("--start-date", default="2016-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7497, help="TWS paper port is commonly 7497; live TWS commonly 7496.")
    parser.add_argument("--client-id", type=int, default=731)
    parser.add_argument("--out-dir", default="data/market")
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)
    if start_date >= end_date:
        raise SystemExit("--start-date must be earlier than --end-date")

    try:
        from ib_async import IB, Stock, util
    except ImportError as exc:
        raise SystemExit("Missing optional dependency. Install with: pip install -r requirements-ibkr.txt") from exc

    ib = IB()
    try:
        print(f"Connecting to IBKR {args.host}:{args.port} in READ-ONLY mode...")
        ib.connect(args.host, args.port, clientId=args.client_id, readonly=True, timeout=10)
        summaries = []
        for symbol in args.symbols:
            summaries.append(download_symbol(ib, util, Stock, symbol.upper(), start_date, end_date, Path(args.out_dir)))
        print("Completed:", summaries)
    finally:
        if ib.isConnected():
            ib.disconnect()


if __name__ == "__main__":
    main()
