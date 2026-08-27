# Swing Screener

A daily swing-trade screener for the **S&P 500, Nasdaq-100 and FTSE 100**.

Every weekday morning a GitHub Action downloads ~1 year of daily bars for
~700 tickers, runs a signal engine over them, and commits the results to
`data/scan.json`. A Claude scheduled task then picks that file up and
republishes a hosted dashboard.

## Signals

| Family | Signal | What it looks for |
|---|---|---|
| Momentum | `ma_crossover` | SMA20 crossed above SMA50 in the last 3 sessions, price above both |
| Momentum | `breakout` | New 55-day closing high on ≥1.5× average volume |
| Momentum | `rel_strength` | 3-month return in the top decile of the universe, above SMA20 |
| Mean reversion | `pullback` | Uptrend intact (above SMA150, SMA50 rising), RSI < 40, within 3% of SMA50 |
| Mean reversion | `oversold_bounce` | Close below the lower Bollinger band, rising SMA50, green reversal day |
| Vol/volume | `volume_spike` | Volume ≥ 2.5× its 20-day average on an up day |
| Vol/volume | `squeeze` | Bollinger bandwidth in the lowest decile of 6 months, above SMA50 |

Each setup gets an ATR-based trade plan: entry at close, stop 1.5×ATR below,
target 2.5×ATR above (≈1.67 reward:risk), plus a confluence score
(number of signals + relative-strength percentile).

## Run locally

```bash
pip install -r requirements.txt
python screener/scan.py        # writes data/scan.json
```

## How the pieces fit

1. `.github/workflows/scan.yml` — runs Tue–Sat at 05:45 UTC (after the US close),
   refreshes constituent lists from Wikipedia, runs the scan, commits `data/scan.json`.
2. `screener/signals.py` — the indicator + signal library (pure pandas/numpy).
3. `screener/scan.py` — downloads data via yfinance and orchestrates the scan.
4. `dashboard/build_dashboard.py` — turns `scan.json` + `dashboard/template.html`
   into a self-contained HTML dashboard.

> Not financial advice — a screening tool that surfaces candidates for further
> analysis, nothing more.
