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
