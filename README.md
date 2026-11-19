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
