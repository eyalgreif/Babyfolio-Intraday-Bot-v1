# IBKR/TWS local market-data input

The CSV provider lets the research engine use historical bars downloaded from
Interactive Brokers without placing API credentials or licensed market data in
the public GitHub repository.

## Files

Place these files locally in `data/market/` (the directory is git-ignored):

- `SPY_5m.csv`
- `SPY_1d.csv`
- `QQQ_5m.csv`
- `QQQ_1d.csv`
- `IWM_5m.csv`
- `IWM_1d.csv`

Each CSV must have the columns:

`timestamp,open,high,low,close,volume`

Timestamps should be in America/New_York. Naive timestamps are interpreted as
New York local time. Export regular-session 5-minute bars and daily bars with
split-consistent adjustments. Include at least 180 calendar days of daily
warm-up before the first intraday date so the 20/50-day trend filter can form.

## Run

```bash
python research/confluence.py --provider csv --data-dir data/market --tickers SPY QQQ IWM
```

The report will be created in `reports/`. Do not commit licensed raw market
data to this public repository; retain it locally and share only the derived
summary/report if your data license permits it.

For continuous history, use IBKR TWS API or Client Portal API with the appropriate
market-data subscriptions. Historical data requests are subject to IBKR pacing
and entitlement restrictions. See:
- https://www.interactivebrokers.com/docs/tws-api/doc/md/historical-bars
- https://www.interactivebrokers.com/support/faq/content/28236420


## Recommended path: download directly through IBKR

If you have TWS or IB Gateway installed, the repository also includes a read-only downloader.
It uses `ib_async` and writes the CSV files locally; it does not place or modify orders.

1. Install the optional dependency: `pip install -r requirements-ibkr.txt`.
2. Open TWS or IB Gateway and enable API socket clients. Keep the API in read-only mode.
3. Confirm the API port. Common TWS defaults are 7497 (paper) and 7496 (live); IB Gateway commonly uses 4002 (paper) and 4001 (live).
4. Run:

```bash
python research/download_ibkr.py --port 7497 --start-date 2016-01-01 --symbols SPY QQQ IWM
```

For IB Gateway, use the port configured in Gateway instead of assuming the TWS defaults.
The script connects with `readonly=True`, retrieves 5-minute TRADES bars for regular US
market hours plus daily bars, and writes to `data/market/`. Then run:

```bash
python research/confluence.py --provider csv --data-dir data/market --tickers SPY QQQ IWM
```

IBKR historical API access requires the applicable market-data entitlements. The API may
filter some trades that occur away from the NBBO, so its reported bar volume can be lower
than an unfiltered exchange feed. Treat volume-derived confirmations as proxies and
compare them to a consolidated source before promoting a volume-sensitive strategy.
