# Babyfolio Intraday Bot

Research-only intraday trading strategy prototype inspired by the Babyfolio article *Technical Analysis: Keeping It Simple*.

## Safety first
This project is for research and backtesting only. It does not place live trades. Historical results do not guarantee future performance.

## Planned v1
- 5-minute US stock candles with a daily trend filter
- Long-only VWAP reclaim and EMA pullback strategies
- Risk-based position sizing and daily loss limits
- Backtest report with transaction-cost assumptions
- GitHub Actions workflow to run tests and publish a report artifact

See the project workflow and implementation as they are added.

## Confluence research branch

The `research-confluence-v1` branch adds a separate research-only engine in
`research/confluence.py`. It compares VWAP reclaim variants and a trend-pullback
variant on SPY, QQQ, and IWM using 5-minute OHLCV bars.

Important limitations:
- Yahoo Finance intraday history is short/limited; the initial 60-day run is exploratory, not multi-year validation.
- Relative volume is a same-time-of-day candle-volume proxy, **not** order-flow imbalance or Level-2 data.
- Signals are evaluated at a completed bar and enter at the next bar open.
- If stop and target are both touched within a bar, stop is assumed first.
- Round-trip cost sensitivity is shown at 2, 5, and 8 basis points; these are assumptions, not broker quotes.
- No live trading or broker integration is included.

Run locally: `pip install -r requirements.txt && pytest -q && python research/confluence.py`.

## Multi-year data research

The confluence branch supports Alpaca historical bars via `research/data_provider.py`.
To enable it, create an Alpaca account/API key and add these **repository Actions secrets**:
- `APCA_API_KEY_ID`
- `APCA_API_SECRET_KEY`

The workflow detects these secrets and uses Alpaca automatically; without them it falls back
to the short Yahoo Finance sample. Secrets are read from the environment and are never written
to the repository or report.

For this historical backtest, the default is now **`sip`**, not `iex`. Alpaca's Market Data FAQ
states that historical SIP queries can be made on the Basic plan when the requested end time
is at least 15 minutes old; the free plan's real-time feed remains IEX-only. Our research job
requests completed historical bars, so it should use the consolidated SIP feed without paying
for live SIP access. Verify the account's entitlement with the first run; if Alpaca rejects the
SIP request, use the IBKR CSV route below. Do not subscribe to the $99/month plan just for this
first historical research pass.

Sources: [Alpaca Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) and
[Historical Bars API](https://docs.alpaca.markets/us/reference/stockbars).

Run manually or locally:
`python research/confluence.py --provider alpaca --feed sip --start-date 2016-01-01 --tickers SPY QQQ IWM`

The report records per-symbol date coverage and chronological diagnostics:
2016–2021 development, 2022–2024 validation, 2025–2026-08-26 historical holdout,
and a separately labelled recent pilot window already inspected. These are useful
diagnostics but **not a substitute for a pre-registered forward paper-trading holdout**.


### Preferred data route if you already use IBKR

The branch includes a read-only local downloader at `research/download_ibkr.py`. Install
`requirements-ibkr.txt`, enable the TWS/IB Gateway API in read-only mode, then run:

```bash
python research/download_ibkr.py --port 7497 --start-date 2016-01-01 --symbols SPY QQQ IWM
python research/confluence.py --provider csv --data-dir data/market --tickers SPY QQQ IWM
```

It stores licensed market data only in the locally git-ignored `data/market/` folder.
See [research/IBKR_DATA.md](research/IBKR_DATA.md) for setup and entitlement notes.
