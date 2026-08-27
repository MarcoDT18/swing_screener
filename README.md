# Swing Screener — "Signal Desk"

A fully automated swing-trade screener for the **S&P 500, Nasdaq-100 and FTSE 100**.

Every weekday morning (05:45 UTC, Tue–Sat) a GitHub Action downloads about a year
of daily prices for ~700 stocks, checks each one for classic swing-trade patterns,
and deploys the results as a dashboard on GitHub Pages:

**Live dashboard → https://marcodt18.github.io/swing_screener/**

No servers, no manual steps: GitHub's free Actions runners do the scanning and the
publishing.

## What it looks for

| Family | Signal | Plain English |
|---|---|---|
| Trending up | `ma_crossover` | The 20-day average price just rose above the 50-day — often the start of a new climb |
| Trending up | `breakout` | Highest close in 55 trading days, on well-above-average volume |
| Trending up | `rel_strength` | 3-month gain in the top 10% of all stocks tracked, and still above its short-term trend |
| Buy the dip | `pullback` | A steady climber resting at its usual support (above the 150-day average, RSI < 40, near the 50-day) |
| Buy the dip | `oversold_bounce` | Fell below its normal Bollinger range, then closed higher — first hint of a rebound |
| Unusual activity | `volume_spike` | 2.5× normal volume on a rising day |
| Unusual activity | `squeeze` | Quietest trading range in six months while still above trend — calm that often precedes a sharp move |

Each flagged stock gets a trade plan sized to its own volatility (ATR):
buy at the last close, stop 1.5×ATR below, target 2.5×ATR above — roughly a
1.7-to-1 reward-to-risk. A **rank** rewards stocks where several independent
signals agree and recent performance is strong. The published list keeps the
top 120 overall plus the best 15 from every family, so quieter families are
never crowded out.

## How it's put together

1. `.github/workflows/scan.yml` — the schedule. Refreshes index constituent
   lists from Wikipedia, runs the scan, commits `data/scan.json`, builds the
   dashboard and deploys it to GitHub Pages.
2. `screener/signals.py` — the indicator and signal library (pure pandas/numpy:
   SMA, Wilder RSI, ATR, Bollinger bands, and the seven checks above).
3. `screener/scan.py` — downloads prices via yfinance, runs every check,
   ranks the results, writes `data/scan.json`.
4. `dashboard/template.html` + `dashboard/build_dashboard.py` — inject the JSON
   into a self-contained HTML page at `site/index.html`.

## Run it yourself

```bash
pip install -r requirements.txt
python screener/scan.py                 # writes data/scan.json (~2 min)
python dashboard/build_dashboard.py     # writes site/index.html — open in a browser
```

> **Not financial advice.** This is a mechanical screen of end-of-day data — it
> tells you where to look, not what to buy. Check the chart, the news and your
> position sizing before trading anything.
