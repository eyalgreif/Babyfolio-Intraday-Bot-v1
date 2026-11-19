"""Alpaca historical OHLCV data adapter.

Credentials are read only from APCA_API_KEY_ID and APCA_API_SECRET_KEY.
No credentials are written to disk or logged.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
import os
import time
from typing import Any

import pandas as pd
import requests

BASE_URL = "https://data.alpaca.markets/v2/stocks/bars"


def _parse_bars(payload: dict[str, Any], symbol: str) -> pd.DataFrame:
    bars_by_symbol = payload.get("bars", {})
    rows = bars_by_symbol.get(symbol, []) if isinstance(bars_by_symbol, dict) else []
    if not rows:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(rows).rename(columns={
        "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"
    })
    required = ["t", "open", "high", "low", "close", "volume"]
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(f"Alpaca response missing fields: {missing}")
    frame["timestamp"] = pd.to_datetime(frame["t"], utc=True)
    frame = frame.set_index("timestamp")[["open", "high", "low", "close", "volume"]]
    for col in ["open", "high", "low", "close", "volume"]:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame.dropna().sort_index()


def fetch_alpaca_bars(
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    feed: str = "sip",
    api_key: str | None = None,
    secret_key: str | None = None,
    session: requests.Session | None = None,
) -> pd.DataFrame:
    """Fetch all pages of historical bars for one symbol."""
    key = api_key or os.getenv("APCA_API_KEY_ID")
    secret = secret_key or os.getenv("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise RuntimeError(
            "Alpaca credentials missing. Set APCA_API_KEY_ID and APCA_API_SECRET_KEY "
            "as environment variables or GitHub Actions secrets."
        )
    if feed not in {"iex", "sip"}:
        raise ValueError("feed must be 'iex' or 'sip'")
    client = session or requests.Session()
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    params: dict[str, Any] = {
        "symbols": symbol,
        "timeframe": timeframe,
        "start": start,
        "end": end,
        "limit": 10000,
        "adjustment": "split",
        "feed": feed,
        "sort": "asc",
    }
    pages: list[pd.DataFrame] = []
    page_count = 0
    while True:
        page_count += 1
        if page_count > 500:
            raise RuntimeError(f"Pagination safety stop for {symbol}: exceeded 500 pages")
        response = client.get(BASE_URL, params=params, headers=headers, timeout=45)
        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", "2"))
            time.sleep(min(max(retry_after, 1.0), 30.0))
            continue
        if not response.ok:
            detail = response.text[:500].replace("\n", " ")
            raise RuntimeError(f"Alpaca HTTP {response.status_code} for {symbol}/{feed}: {detail}")
        payload = response.json()
        frame = _parse_bars(payload, symbol)
        if not frame.empty:
            pages.append(frame)
        token = payload.get("next_page_token")
        if not token:
            break
        params["page_token"] = token
    if not pages:
        raise RuntimeError(f"Alpaca returned no {timeframe} bars for {symbol} ({start} to {end}, {feed})")
    result = pd.concat(pages).sort_index()
    result = result[~result.index.duplicated(keep="last")]
    return result


def download_alpaca_symbol(
    symbol: str,
    start: str,
    end: str,
    feed: str = "sip",
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Download 5-minute bars plus daily regime history for the same feed."""
    start_dt = datetime.fromisoformat(start).date()
    daily_start = (start_dt - timedelta(days=180)).isoformat()
    intra = fetch_alpaca_bars(symbol, "5Min", start, end, feed=feed)
    daily = fetch_alpaca_bars(symbol, "1Day", daily_start, end, feed=feed)
    daily.index = daily.index.tz_convert("America/New_York").tz_localize(None)
    metadata = {
        "provider": "alpaca",
        "feed": feed,
        "start_requested": start,
        "end_requested": end,
        "intraday_bars": int(len(intra)),
        "daily_bars": int(len(daily)),
        "intraday_first": intra.index.min().isoformat() if len(intra) else None,
        "intraday_last": intra.index.max().isoformat() if len(intra) else None,
        "daily_first": daily.index.min().isoformat() if len(daily) else None,
        "daily_last": daily.index.max().isoformat() if len(daily) else None,
    }
    return intra, daily, metadata


def load_csv_symbol(symbol: str, data_dir: str = "data/market") -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Load IBKR/TWS-exported 5-minute and daily OHLCV CSV files.

    Expected files: {symbol}_5m.csv and {symbol}_1d.csv.
    Each file must contain timestamp, open, high, low, close, volume columns.
    Timestamps may be timezone-aware or naive; naive timestamps are treated as America/New_York.
    """
    from pathlib import Path
    root = Path(data_dir)
    intraday_path = root / f"{symbol.upper()}_5m.csv"
    daily_path = root / f"{symbol.upper()}_1d.csv"

    def read_file(path: Path) -> pd.DataFrame:
        if not path.exists():
            raise FileNotFoundError(f"Required IBKR CSV not found: {path}")
        frame = pd.read_csv(path)
        frame.columns = [str(c).strip().lower() for c in frame.columns]
        required = {"timestamp", "open", "high", "low", "close", "volume"}
        missing = required.difference(frame.columns)
        if missing:
            raise ValueError(f"{path} missing columns: {sorted(missing)}")
        idx = pd.to_datetime(frame.pop("timestamp"), errors="coerce")
        if idx.isna().any():
            raise ValueError(f"{path} contains invalid timestamps")
        if idx.dt.tz is None:
            idx = idx.dt.tz_localize("America/New_York", ambiguous="infer", nonexistent="shift_forward")
        else:
            idx = idx.dt.tz_convert("America/New_York")
        frame.index = pd.DatetimeIndex(idx)
        frame = frame[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
        frame = frame.dropna().sort_index()
        return frame[~frame.index.duplicated(keep="last")]

    intra = read_file(intraday_path)
    daily = read_file(daily_path)
    daily.index = daily.index.tz_localize(None).normalize()
    metadata = {
        "provider": "csv",
        "feed": "local CSV (e.g. IBKR/TWS export)",
        "start_requested": None,
        "end_requested": None,
        "intraday_bars": int(len(intra)),
        "daily_bars": int(len(daily)),
        "intraday_first": intra.index.min().isoformat() if len(intra) else None,
        "intraday_last": intra.index.max().isoformat() if len(intra) else None,
        "daily_first": daily.index.min().isoformat() if len(daily) else None,
        "daily_last": daily.index.max().isoformat() if len(daily) else None,
        "intraday_file": str(intraday_path),
        "daily_file": str(daily_path),
    }
    return intra, daily, metadata
