"""
Backtest v2 — replays every signal over years of history and asks:
did entering on this signal beat entering at random, with identical exits?

New in v2
---------
* Liquidity filter: trades require a 20-day median traded value above
  $5M (US) / £2M (UK) at signal time — reversal profits vanish in
  illiquid names once costs bite (de Groot, Huij & Zhou 2011).
* Hold-period sweep: each signal is simulated at several holding periods.
* Momentum sleeve: a new cross-sectional 6-month momentum signal with
  trend-following exits (wider stop, no profit target, months-long holds)
  — momentum lives at 3-12 month horizons, not 20 days.
* Walk-forward: the best hold period is CHOSEN on the first 60% of history
  (train) and JUDGED on the last 40% (test). Out-of-sample results are the
  only ones that count; a Bonferroni-adjusted p-value accounts for the
  number of configurations tried.

Everything is reported in R multiples: profit in units of initial risk.
Entries at next day's open (no look-ahead); stop assumed first on ambiguous
days (pessimistic); 0.1% costs per side.

Run:  python backtest/backtest.py            (yfinance, ~5y, needs network)
      python backtest/backtest.py --csv F    (offline long CSV for testing)
Writes data/backtest.json.
"""

import argparse
import json
import os
import sys
import datetime as dt
from math import erf, sqrt

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from screener.signals import rsi, atr, sma, bollinger  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

COST = 0.001                    # 0.1% per side
YEARS = "5y"
TRAIN_FRAC = 0.6                # walk-forward split point
MIN_TRAIN_TRADES = 80           # need this many train trades to pick a hold
LIQ_US, LIQ_UK = 5e6, 2e6       # min 20d median traded value ($ / £)
RNG = np.random.default_rng(42)

# per-signal exit config: candidate holds, stop/target in ATRs, has profit target
DEFAULT_CFG = {"holds": [5, 10, 20], "stop": 1.5, "tgt": 2.5, "use_target": True}
SLEEVE_CFG = {"holds": [40, 60, 80], "stop": 3.0, "tgt": None, "use_target": False}
CFG = {
    "ma_crossover": DEFAULT_CFG, "breakout": DEFAULT_CFG, "rel_strength": DEFAULT_CFG,
    "pullback": DEFAULT_CFG, "oversold_bounce": DEFAULT_CFG,
    "volume_spike": DEFAULT_CFG, "squeeze": DEFAULT_CFG,
    "momentum_6m": SLEEVE_CFG,
}
DEFAULT_HOLD = 20               # headline tables use this (continuity with v1)
SLEEVE_DEFAULT_HOLD = 60


# ---------- vectorized signal masks (same formulas as the live scanner) ----------

def signal_masks(df):
    close, opn, vol = df["Close"], df["Open"], df["Volume"]
    s20, s50, s150 = sma(close, 20), sma(close, 50), sma(close, 150)
    r = rsi(close)
    _, _, lower, bw = bollinger(close)
    vol_avg = vol.shift(1).rolling(20).mean()
    above = s20 > s50
    masks = {
        "ma_crossover": (above & ~above.shift(1).fillna(False)) & (close > s20),
        "breakout": (close > close.shift(1).rolling(55).max()) & (vol > 1.5 * vol_avg),
        "pullback": (close > s150) & (s50 > s50.shift(10)) & (r < 40)
                    & ((close / s50 - 1).abs() < 0.03),
        "oversold_bounce": (close.shift(1) < lower.shift(1))
                    & (s50 > s50.shift(10)) & (close > opn),
        "volume_spike": (vol > 2.5 * vol_avg) & (close > close.shift(1)),
        "squeeze": (bw <= bw.rolling(126).quantile(0.10)) & (close > s50),
    }
    return {k: m.fillna(False) for k, m in masks.items()}


def cross_sectional_masks(closes):
    """Masks needing the whole universe: 3m relative strength, 6m momentum."""
    out = {}
    ret63 = closes / closes.shift(63) - 1
    out["rel_strength"] = (ret63.rank(axis=1, pct=True) >= 0.90) \
        & (closes > closes.rolling(20).mean())
    ret126 = closes / closes.shift(126) - 1
    out["momentum_6m"] = (ret126.rank(axis=1, pct=True) >= 0.90) \
        & (closes > closes.rolling(50).mean())
    return out


# ---------- trade simulation ----------

def simulate(df, idx, a, hold, stop_mult, tgt_mult, use_target):
    n = len(df)
    if idx + 2 >= n or a != a or a <= 0:
        return None
    entry = float(df["Open"].iloc[idx + 1])
    if entry <= 0:
        return None
    stop = entry - stop_mult * a
    risk = entry - stop
    target = entry + tgt_mult * a if use_target else None
    last = min(idx + 1 + hold, n - 1)
    for j in range(idx + 1, last + 1):
        if float(df["Low"].iloc[j]) <= stop:          # stop first — pessimistic
            exit_px = stop
            break
        if use_target and float(df["High"].iloc[j]) >= target:
            exit_px = target
            break
    else:
        exit_px = float(df["Close"].iloc[last])
        j = last
    gross_r = (exit_px - entry) / risk
    return gross_r - COST * (entry + exit_px) / risk, j - idx


def stats(rs):
    rs = np.asarray(rs, dtype=float)
    if len(rs) == 0:
        return None
    return {
        "trades": int(len(rs)),
        "win_rate": round(float((rs > 0).mean()) * 100, 1),
        "avg_r": round(float(rs.mean()), 3),
        "median_r": round(float(np.median(rs)), 3),
        "profit_factor": round(float(rs[rs > 0].sum() / max(1e-9, -rs[rs <= 0].sum())), 2),
    }


def p_one_sided(sig_rs, base_rs):
    """P(edge is luck): one-sided difference-of-means z-test."""
    a, b = np.asarray(sig_rs, float), np.asarray(base_rs, float)
    if len(a) < 30 or len(b) < 30:
        return None
    se = sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    if se == 0:
        return None
    z = (a.mean() - b.mean()) / se
    return round(1 - 0.5 * (1 + erf(z / sqrt(2))), 3)


def regime_series(index_close):
    return (index_close > index_close.rolling(200).mean()).fillna(False)


# ---------- the run ----------

def run(frames, indices=None):
    closes = pd.DataFrame({t: d["Close"] for t, d in frames.items()})
    cs_masks = cross_sectional_masks(closes)

    regimes = {}
    if indices:
        for key, ser in indices.items():
            if ser is not None and len(ser) > 220:
                regimes[key] = regime_series(ser)

    def regime_at(ticker, date):
        reg = regimes.get("uk" if ticker.endswith(".L") else "us")
        if reg is None:
            return None
        try:
            pos = reg.index.get_indexer([date], method="ffill")[0]
            return bool(reg.iloc[pos]) if pos >= 0 else None
        except Exception:
            return None

    # -- collect signal events once, then simulate each event at every hold --
    rows = []          # dicts: signal, ticker, date, risk_on, r@hold...
    for t, df in frames.items():
        masks = signal_masks(df)
        for sig in ("rel_strength", "momentum_6m"):
            if t in cs_masks[sig].columns:
                masks[sig] = cs_masks[sig][t].reindex(df.index).fillna(False)
        a_series = atr(df)
        liq = (df["Close"] * df["Volume"]).rolling(20).median()
        liq_min = LIQ_UK * 100 if t.endswith(".L") else LIQ_US   # pence vs $
        open_until = {}
        for sig, mask in masks.items():
            cfg = CFG[sig]
            hits = np.flatnonzero(mask.values)
            for idx in hits:
                if idx < 160 or idx <= open_until.get(sig, -1):
                    continue
                if not (liq.iloc[idx] > liq_min):                 # liquidity gate
                    continue
                a = float(a_series.iloc[idx])
                res_by_hold, ok = {}, True
                for h in cfg["holds"]:
                    res = simulate(df, idx, a, h, cfg["stop"], cfg["tgt"], cfg["use_target"])
                    if res is None:
                        ok = False
                        break
                    res_by_hold[h] = res[0]
                if not ok:
                    continue
                open_until[sig] = idx + max(cfg["holds"])         # no overlap at any hold
                rows.append({"signal": sig, "ticker": t, "date": df.index[idx],
                             "risk_on": regime_at(t, df.index[idx]), **res_by_hold})
        print(f"  scanned {t}", flush=True) if False else None
    tdf = pd.DataFrame(rows)
    print(f"events: {len(tdf)}", flush=True)

    # -- random baselines, one pool per (hold, exit-style) config --
    tickers = list(frames)
    def make_baseline(hold, stop_m, tgt_m, use_t, n=12000):
        out = []
        while len(out) < n:
            t = tickers[RNG.integers(len(tickers))]
            df = frames[t]
            if len(df) < 200:
                continue
            idx = int(RNG.integers(160, len(df) - 2))
            res = simulate(df, idx, float(atr(df).iloc[idx]), hold, stop_m, tgt_m, use_t)
            if res:
                out.append((df.index[idx], res[0]))
        return pd.DataFrame(out, columns=["date", "r"])

    baselines = {}
    for h in DEFAULT_CFG["holds"]:
        baselines[("d", h)] = make_baseline(h, DEFAULT_CFG["stop"], DEFAULT_CFG["tgt"], True)
    for h in SLEEVE_CFG["holds"]:
        baselines[("s", h)] = make_baseline(h, SLEEVE_CFG["stop"], None, False)
    print("baselines done", flush=True)

    def bl(sig, hold):
        return baselines[("s" if sig == "momentum_6m" else "d", hold)]

    # -- headline tables at the default hold (continuity with v1) --
    def default_hold(sig):
        return SLEEVE_DEFAULT_HOLD if sig == "momentum_6m" else DEFAULT_HOLD

    by_signal = {}
    for sig, g in tdf.groupby("signal"):
        h = default_hold(sig)
        rs = g[h].tolist()
        s = stats(rs)
        s["hold"] = h
        s["baseline_avg_r"] = round(float(bl(sig, h)["r"].mean()), 3)
        s["p_value"] = p_one_sided(rs, bl(sig, h)["r"])
        if g["risk_on"].notna().any():
            s["regime"] = {
                "risk_on": stats(g.loc[g["risk_on"] == True, h].tolist()),
                "risk_off": stats(g.loc[g["risk_on"] == False, h].tolist()),
            }
        by_signal[sig] = s

    # -- walk-forward: choose hold on train, judge on test --
    dates_sorted = tdf["date"].sort_values()
    split_date = dates_sorted.iloc[int(len(dates_sorted) * TRAIN_FRAC)]
    n_configs = sum(len(c["holds"]) for c in CFG.values())
    walkforward = {"split_date": str(pd.Timestamp(split_date).date()),
                   "train_frac": TRAIN_FRAC, "configs_tested": n_configs,
                   "signals": {}}
    for sig, g in tdf.groupby("signal"):
        cfg = CFG[sig]
        train, test = g[g["date"] < split_date], g[g["date"] >= split_date]
        if len(train) < MIN_TRAIN_TRADES or len(test) < 30:
            continue
        best_h = max(cfg["holds"], key=lambda h: train[h].mean())
        test_rs = test[best_h].tolist()
        base = bl(sig, best_h)
        base_test = base.loc[base["date"] >= split_date, "r"]
        if len(base_test) < 100:
            base_test = base["r"]
        p = p_one_sided(test_rs, base_test)
        walkforward["signals"][sig] = {
            "chosen_hold": int(best_h),
            "train": stats(train[best_h].tolist()),
            "test": stats(test_rs),
            "test_baseline_avg_r": round(float(base_test.mean()), 3),
            "p_test": p,
            "p_adjusted": (None if p is None else round(min(1.0, p * n_configs), 3)),
            # raw test-era trade outcomes, for the Monte Carlo simulator
            "oos_r": [round(float(x), 3) for x in test_rs],
            "oos_months": round((test["date"].max() - test["date"].min()).days / 30.44, 1),
        }

    # -- regime overall + by year (default holds) --
    tdf["_r"] = [row[default_hold(s)] for s, row in zip(tdf["signal"], tdf.to_dict("records"))]
    regime_overall = None
    if tdf["risk_on"].notna().any():
        regime_overall = {
            "risk_on": stats(tdf.loc[tdf["risk_on"] == True, "_r"].tolist()),
            "risk_off": stats(tdf.loc[tdf["risk_on"] == False, "_r"].tolist()),
        }
    tdf["year"] = tdf["date"].astype(str).str[:4]
    by_year = {y: stats(g["_r"].tolist()) for y, g in tdf.groupby("year")}

    b20 = baselines[("d", DEFAULT_HOLD)]["r"]
    return {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "period": {"start": str(tdf["date"].min().date()), "end": str(tdf["date"].max().date())},
        "rules": {"hold_days": DEFAULT_HOLD, "atr_stop": DEFAULT_CFG["stop"],
                  "atr_target": DEFAULT_CFG["tgt"], "cost_per_side_pct": COST * 100,
                  "liquidity_min_usd": LIQ_US, "liquidity_min_gbp": LIQ_UK,
                  "sleeve": {"hold_days": SLEEVE_DEFAULT_HOLD, "atr_stop": SLEEVE_CFG["stop"],
                              "target": "none (trend-following exit)"}},
        "tickers_tested": len(frames),
        "total_trades": int(len(tdf)),
        "baseline": stats(b20),
        "regime_overall": regime_overall,
        "by_signal": by_signal,
        "by_year": by_year,
        "walkforward": walkforward,
    }


# ---------- data loading ----------

def load_yfinance():
    import yfinance as yf
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
    idx = yf.download(["^GSPC", "^FTSE"], period=YEARS, interval="1d",
                      group_by="ticker", auto_adjust=True, threads=True, progress=False)
    indices = {}
    for key, sym in (("us", "^GSPC"), ("uk", "^FTSE")):
        try:
            indices[key] = idx[sym]["Close"].dropna()
        except KeyError:
            indices[key] = None
    return frames, indices


def load_csv(path):
    raw = pd.read_csv(path, parse_dates=["date"])
    raw = raw.rename(columns=str.capitalize).rename(columns={"Name": "Ticker"})
    frames = {}
    for t, g in raw.groupby("Ticker"):
        df = g.set_index("Date").sort_index()[["Open", "High", "Low", "Close", "Volume"]].dropna()
        if len(df) >= 300:
            frames[t] = df
    proxy = pd.DataFrame({t: d["Close"] / d["Close"].iloc[0] for t, d in frames.items()}).mean(axis=1)
    return frames, {"us": proxy, "uk": None}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", help="offline long CSV instead of yfinance")
    args = ap.parse_args()
    frames, indices = load_csv(args.csv) if args.csv else load_yfinance()
    print(f"Backtesting {len(frames)} tickers…", flush=True)
    payload = run(frames, indices)
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "data", "backtest.json"), "w") as f:
        json.dump(payload, f, indent=1)
    print(json.dumps({k: payload[k] for k in ("total_trades", "baseline")}, indent=1))
    print("\nWALK-FORWARD (out-of-sample):")
    for s, v in sorted(payload["walkforward"]["signals"].items(),
                       key=lambda kv: -(kv[1]["test"]["avg_r"])):
        print(f"{s:16} hold {v['chosen_hold']:>2}d  train {v['train']['avg_r']:+.3f}"
              f"  TEST {v['test']['avg_r']:+.3f} (base {v['test_baseline_avg_r']:+.3f},"
              f" p {v['p_test']}, adj {v['p_adjusted']}, n {v['test']['trades']})")
