"""
Backtest — replays every signal over years of history and asks one question:
did entering on this signal beat entering at random, with identical exits?

Method
------
* Signals are recomputed for EVERY day in history with vectorized pandas
  (same formulas as screener/signals.py, applied to whole series).
* A signal on day t opens a simulated trade at the NEXT day's open
  (you'd see the signal after the close, so this avoids look-ahead bias).
* Exits mirror the live trade plan: stop = entry - 1.5*ATR(t),
  target = entry + 2.5*ATR(t), else time-exit at the close 20 sessions later.
  If a day's range touches both stop and target, the stop is assumed first
  (the conservative choice).
* Costs: 0.1% per side (0.2% round trip).
* Baseline: the same number of trades entered on RANDOM ticker-days with the
  identical exit rules. If a signal can't beat that, the signal adds nothing.
* Results are reported in R multiples: profit measured in units of the initial
  risk (entry minus stop). +1R means you made what you risked.

Run:  python backtest/backtest.py            (uses yfinance, ~5y, needs network)
      python backtest/backtest.py --csv F    (offline: long CSV with
                                              date,open,high,low,close,volume,Name)
Writes data/backtest.json.
"""

import argparse
import json
import os
import sys
import datetime as dt

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from screener.signals import rsi, atr, sma, bollinger  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

HOLD_DAYS = 20          # time exit after this many sessions
ATR_STOP, ATR_TGT = 1.5, 2.5
COST = 0.001            # 0.1% per side
YEARS = "5y"
RNG = np.random.default_rng(42)


# ---------- vectorized signal masks (same formulas as the live scanner) ----------

def signal_masks(df):
    """Returns {signal_name: boolean Series over df.index}."""
    close, opn, high, low, vol = df["Close"], df["Open"], df["High"], df["Low"], df["Volume"]
    s20, s50, s150 = sma(close, 20), sma(close, 50), sma(close, 150)
    r = rsi(close)
    _, _, lower, bw = bollinger(close)
    vol_avg = vol.shift(1).rolling(20).mean()

    above = s20 > s50
    masks = {
        "ma_crossover": (above & ~above.shift(1).fillna(False)) & (close > s20),
        "breakout": (close > close.shift(1).rolling(55).max())
                    & (vol > 1.5 * vol_avg),
        "pullback": (close > s150) & (s50 > s50.shift(10)) & (r < 40)
                    & ((close / s50 - 1).abs() < 0.03),
        "oversold_bounce": (close.shift(1) < lower.shift(1))
                    & (s50 > s50.shift(10)) & (close > opn),
        "volume_spike": (vol > 2.5 * vol_avg) & (close > close.shift(1)),
        "squeeze": (bw <= bw.rolling(126).quantile(0.10)) & (close > s50),
    }
    return {k: m.fillna(False) for k, m in masks.items()}


def rel_strength_mask(closes):
    """Cross-sectional: 63-day return in the top decile AND above SMA20.
    `closes` is a DataFrame (dates x tickers). Returns same-shaped bool frame."""
    ret63 = closes / closes.shift(63) - 1
    pct = ret63.rank(axis=1, pct=True)
    above20 = closes > closes.rolling(20).mean()
    return (pct >= 0.90) & above20


# ---------- trade simulation ----------

def simulate(df, idx, a):
    """Simulate one trade signalled at positional index `idx`.
    Returns (R multiple, holding days, exit reason) or None near data end."""
    n = len(df)
    if idx + 2 >= n or a != a or a <= 0:
        return None
    entry = float(df["Open"].iloc[idx + 1])
    if entry <= 0:
        return None
    stop = entry - ATR_STOP * a
    target = entry + ATR_TGT * a
    risk = entry - stop
    last = min(idx + 1 + HOLD_DAYS, n - 1)
    for j in range(idx + 1, last + 1):
        lo, hi = float(df["Low"].iloc[j]), float(df["High"].iloc[j])
        if lo <= stop:                       # stop first — conservative
            exit_px, reason = stop, "stop"
            break
        if hi >= target:
            exit_px, reason = target, "target"
            break
    else:
        exit_px, reason = float(df["Close"].iloc[last]), "time"
        j = last
    gross_r = (exit_px - entry) / risk
    cost_r = COST * (entry + exit_px) / risk  # both sides
    return gross_r - cost_r, j - idx, reason


def run(frames):
    """frames: {ticker: OHLCV DataFrame}. Returns the results payload."""
    closes = pd.DataFrame({t: d["Close"] for t, d in frames.items()})
    rs_frame = rel_strength_mask(closes)

    trades = []            # (signal, ticker, date, R, hold, reason)
    for t, df in frames.items():
        masks = signal_masks(df)
        if t in rs_frame.columns:
            masks["rel_strength"] = rs_frame[t].reindex(df.index).fillna(False)
        a_series = atr(df)
        open_until = {}    # signal -> pos index until which trade is open
        for sig, mask in masks.items():
            hits = np.flatnonzero(mask.values)
            for idx in hits:
                if idx < 160:              # warm-up for indicators
                    continue
                if idx <= open_until.get(sig, -1):   # one open trade per signal
                    continue
                res = simulate(df, idx, float(a_series.iloc[idx]))
                if res is None:
                    continue
                r_mult, hold, reason = res
                open_until[sig] = idx + hold
                trades.append((sig, t, str(df.index[idx].date()), r_mult, hold, reason))

    # ---------- random baseline: same trade count, same exits ----------
    tickers = list(frames)
    base = []
    n_base = min(len(trades), 20000)
    while len(base) < n_base:
        t = tickers[RNG.integers(len(tickers))]
        df = frames[t]
        if len(df) < 200:
            continue
        idx = int(RNG.integers(160, len(df) - 2))
        res = simulate(df, idx, float(atr(df).iloc[idx]))
        if res:
            base.append(res[0])

    def stats(rs):
        rs = np.array(rs, dtype=float)
        if len(rs) == 0:
            return None
        return {
            "trades": int(len(rs)),
            "win_rate": round(float((rs > 0).mean()) * 100, 1),
            "avg_r": round(float(rs.mean()), 3),
            "median_r": round(float(np.median(rs)), 3),
            "profit_factor": round(float(rs[rs > 0].sum() / max(1e-9, -rs[rs <= 0].sum())), 2),
        }

    by_signal = {}
    tdf = pd.DataFrame(trades, columns=["signal", "ticker", "date", "r", "hold", "reason"])
    for sig, g in tdf.groupby("signal"):
        s = stats(g["r"].tolist())
        s["avg_hold"] = round(float(g["hold"].mean()), 1)
        s["exit_mix"] = {k: int(v) for k, v in g["reason"].value_counts().items()}
        by_signal[sig] = s

    tdf["year"] = tdf["date"].str[:4]
    by_year = {y: stats(g["r"].tolist()) for y, g in tdf.groupby("year")}

    return {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "period": {"start": tdf["date"].min(), "end": tdf["date"].max()},
        "rules": {"hold_days": HOLD_DAYS, "atr_stop": ATR_STOP, "atr_target": ATR_TGT,
                  "cost_per_side_pct": COST * 100},
        "tickers_tested": len(frames),
        "total_trades": int(len(tdf)),
        "baseline": stats(base),
        "by_signal": by_signal,
        "by_year": by_year,
    }


# ---------- data loading ----------

def load_yfinance():
    import yfinance as yf
    sys.path.insert(0, ROOT)
    from screener.scan import load_universe
    uni = load_universe()
    tickers = sorted(uni)
    frames = {}
    B = 100
    for i in range(0, len(tickers), B):
        chunk = tickers[i:i + B]
        data = yf.download(chunk, period=YEARS, interval="1d", group_by="ticker",
                           auto_adjust=True, threads=True, progress=False)
        for t in chunk:
            try:
                df = data[t].dropna(subset=["Close", "Open", "High", "Low"])
            except KeyError:
                continue
            if len(df) >= 300:
                frames[t] = df
        print(f"  downloaded {min(i + B, len(tickers))}/{len(tickers)}", flush=True)
    return frames


def load_csv(path):
    raw = pd.read_csv(path, parse_dates=["date"])
    raw = raw.rename(columns=str.capitalize).rename(columns={"Name": "Ticker"})
    frames = {}
    for t, g in raw.groupby("Ticker"):
        df = g.set_index("Date").sort_index()[["Open", "High", "Low", "Close", "Volume"]].dropna()
        if len(df) >= 300:
            frames[t] = df
    return frames


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="offline long CSV instead of yfinance")
    args = ap.parse_args()
    frames = load_csv(args.csv) if args.csv else load_yfinance()
    print(f"Backtesting {len(frames)} tickers…", flush=True)
    payload = run(frames)
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "data", "backtest.json"), "w") as f:
        json.dump(payload, f, indent=1)
    print(json.dumps({k: payload[k] for k in ("total_trades", "baseline")}, indent=1))
    for s, v in sorted(payload["by_signal"].items(), key=lambda kv: -kv[1]["avg_r"]):
        print(f"{s:16} trades {v['trades']:6}  win {v['win_rate']:5}%  avgR {v['avg_r']:+.3f}  PF {v['profit_factor']}")
