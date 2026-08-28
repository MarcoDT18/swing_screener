# Research notes — what the quant literature says about this screener

Working notes mapping academic evidence onto Signal Desk's own backtest results.
Written 2026-08. Not advice; a map of where edges have historically lived.

## 1. The sobering base rate

Large studies of retail traders are brutal: Barber, Lee, Liu & Odean's study of the
complete Taiwan market found ~80% of day traders lose money and under 1% are
predictably profitable after costs; Chague & Giovannetti found 97% of persistent
Brazilian futures day traders lost. Swing trading on daily bars is slower and cheaper
than day trading, but the lesson stands: **costs and impatience are the default
destroyers**, and any realistic aim is a small, disciplined edge — not income.

## 2. Where documented edges live (and their timescales)

- **Cross-sectional momentum** (Jegadeesh & Titman 1993): winners over the past
  3–12 months keep outperforming for the next 3–12 months. This is a *months*
  phenomenon. Our 20-day exits are too short to harvest it — consistent with our
  backtest, where momentum-family entries never beat random.
- **Time-series momentum** (Moskowitz, Ooi & Pedersen 2012): an asset's own 12-month
  return predicts its next months, across asset classes. Again: long horizon.
- **Short-term reversal** (Lehmann 1990; Jegadeesh 1990): losers over days-to-weeks
  bounce. This is the academic cousin of our `oversold_bounce` — the one signal that
  beat random in BOTH our test eras. Caveat from the cost literature (de Groot,
  Huij & Zhou 2011): naive reversal profits are eaten by trading costs unless
  execution is cheap and stocks are liquid — one reason we stick to large caps.
- **Trend/regime filters** (Faber 2007, "A Quantitative Approach to Tactical Asset
  Allocation"): the simple rule *only be long when the index is above its 10-month
  (~200-day) moving average* historically kept equity-like returns while cutting the
  worst drawdowns dramatically. Our own 2022 row (avg −0.198R, every family losing)
  is exactly the year this filter exists for.

## 3. The statistics of not fooling yourself

- **Backtest overfitting** (Bailey & López de Prado, "The Deflated Sharpe Ratio";
  Bailey, Borwein et al., "The Probability of Backtest Overfitting"): try enough
  rules and the best one looks great by chance. Every extra parameter and re-run of
  our backtest raises the bar the survivor must clear.
- **Multiple testing** (Harvey & Liu): hundreds of published "factors" fail to
  replicate; a t-stat of 2 is not enough when many things were tried.
- Practical consequences adopted here: we test few signals, don't tune their
  parameters to the backtest, report an honest random-entry baseline, split results
  by regime and by year, and attach a p-value to every claimed edge.

## 4. What this means for Signal Desk (the roadmap)

1. **Regime filter** (implemented): tag every day risk-on/risk-off by the index vs
   its 200-day average (S&P 500 for US stocks, FTSE 100 for UK), split all backtest
   stats by regime, and show the live regime on the dashboard.
2. **Significance**: report per-signal p-values vs the random baseline, so "edge"
   means "unlikely to be luck", not "positive number".
3. **All implemented in backtest v2**: hold-period sweep (5/10/20d; 40/60/80d for
   the sleeve), liquidity filter ($5M/£2M min daily traded value), a 6-month
   momentum sleeve with trend-following exits (3×ATR stop, no target), and
   walk-forward validation — holds chosen on the first 60% of history, judged on
   the last 40%, with Bonferroni-adjusted p-values for the configurations tried.
   Remaining candidates: promote the sleeve to the live dashboard if it keeps
   surviving out-of-sample; deflated-Sharpe reporting; position-sizing rules.

## Sources

- Faber (2007), A Quantitative Approach to Tactical Asset Allocation — SSRN 962461
- Jegadeesh & Titman (1993), Returns to Buying Winners and Selling Losers
- Moskowitz, Ooi & Pedersen (2012), Time Series Momentum — J. Financial Economics
- Lehmann (1990); Jegadeesh (1990) — short-term reversal
- de Groot, Huij & Zhou (2011), Another Look at Trading Costs and Short-Term Reversal Profits
- Bailey & López de Prado (2014), The Deflated Sharpe Ratio — SSRN 2460551
- Bailey, Borwein, López de Prado & Zhu, The Probability of Backtest Overfitting
- Harvey & Liu, ...and the Cross-Section of Expected Returns
- Barber, Lee, Liu & Odean, Do Individual Day Traders Make Money? Evidence from Taiwan
