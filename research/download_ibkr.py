"""Download IBKR historical bars locally; read-only connection, no order methods used.

IBKR's historical step-size table supports one week of 5-minute bars per request.
This downloader pages backward in weekly chunks and uses one-year daily chunks.
It intentionally runs locally against the user's TWS/IB Gateway; credentials and
licensed market data never enter GitHub Actions or the repository.
"""
from __future__ import annotations

import argparse
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ET = "America/New_York"
INTRADAY_DURATION = "1 W"
DAILY_DURATION = "1 Y"


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
            timestamps = timestamps.dt.tz_convert(ET).dt.tz_localize(None)
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


def _fetch_intraday(ib, util, contract, symbol: str, start_date: date,
                    request_delay: float, max_batches: int = 900) -> pd.DataFrame:
    chunks = []
    end_cursor = ""
    prior_oldest = None

    for batch in range(max_batches):
        bars = ib.reqHistoricalData(
            contract,
            endDateTime=end_cursor,
            durationStr=INTRADAY_DURATION,
            barSizeSetting="5 mins",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=2,
            keepUpToDate=False,
            timeout=120,
        )
        if not bars:
            break
        normalized = normalize_ibkr_bars(util.df(bars), daily=False)
        if normalized.empty:
            break

        oldest = pd.Timestamp(normalized["timestamp"].min())
        if prior_oldest is not None and oldest >= prior_oldest:
            raise RuntimeError(
                f"{symbol}: historical pagination stopped moving backward "
                f"(previous={prior_oldest}, current={oldest})"
            )
        prior_oldest = oldest
        chunks.append(normalized)
        print(
            f"{symbol}: 5m batch {batch + 1}, {len(normalized):,} bars, "
            f"oldest={oldest.isoformat()}"
        )

        if oldest.tz_convert(ET).date() <= start_date:
            break
        end_cursor = oldest.to_pydatetime() - timedelta(seconds=1)
        if request_delay > 0:
            time.sleep(request_delay)
    else:
        raise RuntimeError(f"{symbol}: exceeded {max_batches} weekly 5m batches")

    if not chunks:
        raise RuntimeError(f"IBKR returned no 5-minute TRADES bars for {symbol}")

    intraday = pd.concat(chunks, ignore_index=True)
    intraday["timestamp"] = pd.to_datetime(intraday["timestamp"], utc=True)
    intraday = intraday.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    et_dates = intraday["timestamp"].dt.tz_convert(ET).dt.date
    intraday = intraday[(et_dates >= start_date)]
    return intraday.reset_index(drop=True)


def _fetch_daily(ib, util, contract, symbol: str, start_date: date,
                 request_delay: float, max_batches: int = 20) -> pd.DataFrame:
    """Fetch daily bars in 1-year chunks to avoid oversized IBKR requests."""
    chunks = []
    end_cursor = ""
    prior_oldest = None

    for batch in range(max_batches):
        bars = ib.reqHistoricalData(
            contract,
            endDateTime=end_cursor,
            durationStr=DAILY_DURATION,
            barSizeSetting="1 day",
            whatToShow="TRADES",
            useRTH=True,
            formatDate=1,
            keepUpToDate=False,
            timeout=120,
        )
        if not bars:
            break
        normalized = normalize_ibkr_bars(util.df(bars), daily=True)
        if normalized.empty:
            break

        oldest = pd.Timestamp(normalized["timestamp"].min()).normalize()
        if prior_oldest is not None and oldest >= prior_oldest:
            raise RuntimeError(
                f"{symbol}: daily pagination stopped moving backward "
                f"(previous={prior_oldest}, current={oldest})"
            )
        prior_oldest = oldest
        chunks.append(normalized)
        print(
            f"{symbol}: daily batch {batch + 1}, {len(normalized):,} bars, "
            f"oldest={oldest.date()}"
        )

        if oldest.date() <= start_date:
            break
        previous_day = oldest - pd.Timedelta(days=1)
        end_cursor = previous_day.strftime("%Y%m%d 23:59:59 US/Eastern")
        if request_delay > 0:
            time.sleep(request_delay)
    else:
        raise RuntimeError(f"{symbol}: exceeded {max_batches} yearly daily batches")

    if not chunks:
        raise RuntimeError(f"IBKR returned no daily TRADES bars for {symbol}")

    daily = pd.concat(chunks, ignore_index=True)
    daily = daily.drop_duplicates("timestamp", keep="last").sort_values("timestamp")
    daily = daily[
        (daily["timestamp"].dt.date >= start_date)
    ].reset_index(drop=True)
    return daily


def download_symbol(ib, util, Stock, symbol: str, start_date: date, end_date: date,
                    out_dir: Path, request_delay: float = 0.5):
    contract = Stock(symbol, "SMART", "USD")
    qualified = ib.qualifyContracts(contract)
    if not qualified:
        raise RuntimeError(f"IBKR could not qualify contract for {symbol}")
    contract = qualified[0]

    intraday = _fetch_intraday(
        ib, util, contract, symbol, start_date, request_delay=request_delay
    )
    daily_start = start_date - timedelta(days=180)
    daily = _fetch_daily(
        ib, util, contract, symbol, daily_start, request_delay=request_delay
    )

    intraday_et_dates = intraday["timestamp"].dt.tz_convert(ET).dt.date
    intraday = intraday[
        (intraday_et_dates >= start_date) & (intraday_et_dates <= end_date)
    ].copy()
    daily = daily[
        (daily["timestamp"].dt.date >= daily_start)
        & (daily["timestamp"].dt.date <= end_date)
    ].copy()

    out_dir.mkdir(parents=True, exist_ok=True)
    intraday_path = out_dir / f"{symbol}_5m.csv"
    daily_path = out_dir / f"{symbol}_1d.csv"
    intraday.to_csv(intraday_path, index=False, date_format="%Y-%m-%dT%H:%M:%SZ")
    daily.to_csv(daily_path, index=False, date_format="%Y-%m-%d")
    print(
        f"{symbol}: saved {len(intraday):,} 5m bars "
        f"({intraday['timestamp'].min()} to {intraday['timestamp'].max()}) "
        f"and {len(daily):,} daily bars"
    )
    return {
        "symbol": symbol,
        "intraday_bars": len(intraday),
        "daily_bars": len(daily),
        "intraday_path": str(intraday_path),
        "daily_path": str(daily_path),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Download IBKR historical bars locally in read-only mode."
    )
    parser.add_argument("--symbols", nargs="*", default=["SPY"])
    parser.add_argument("--start-date", default="2016-01-01")
    parser.add_argument("--end-date", default=date.today().isoformat())
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port", type=int, default=7497,
        help="TWS paper port is commonly 7497; live TWS commonly 7496.",
    )
    parser.add_argument("--client-id", type=int, default=731)
    parser.add_argument("--out-dir", default="data/market")
    parser.add_argument(
        "--request-delay", type=float, default=0.5,
        help="Seconds to pause between historical requests (default: 0.5).",
    )
    args = parser.parse_args()

    start_date = date.fromisoformat(args.start_date)
    end_date = date.fromisoformat(args.end_date)
    if start_date >= end_date:
        raise SystemExit("--start-date must be earlier than --end-date")
    if args.request_delay < 0:
        raise SystemExit("--request-delay cannot be negative")

    try:
        from ib_async import IB, Stock, util
    except ImportError as exc:
        raise SystemExit(
            "Missing optional dependency. Install with: pip install -r requirements-ibkr.txt"
        ) from exc

    ib = IB()
    try:
        print(
            f"Connecting to IBKR {args.host}:{args.port} in READ-ONLY mode; "
            f"5m requests use {INTRADAY_DURATION} chunks..."
        )
        ib.connect(
            args.host, args.port, clientId=args.client_id,
            readonly=True, timeout=15
        )
        summaries = []
        for symbol in args.symbols:
            summaries.append(
                download_symbol(
                    ib, util, Stock, symbol.upper(), start_date, end_date,
                    Path(args.out_dir), request_delay=args.request_delay,
                )
            )
        print("Completed:", summaries)
    finally:
        if ib.isConnected():
            ib.disconnect()


if __name__ == "__main__":
    main()
