"""
Swing screener — daily scan.

Downloads ~1 year of daily bars for the S&P 500, Nasdaq-100 and FTSE 100,
runs the signal engine over every ticker, and writes data/scan.json.

Run locally:  python -m screener.scan
In CI this runs on a weekday schedule (see .github/workflows/scan.yml).
"""

import csv
import json
import os
import sys
import datetime as dt

import pandas as pd
import yfinance as yf

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from screener.signals import scan_ticker, trade_plan  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_PRICE = 3.0          # skip near-penny data glitches
BATCH = 100              # tickers per yfinance download call


def load_universe():
    """Returns {ticker: {"name":..., "universe":...}} for all three lists."""
    uni = {}
    with open(os.path.join(ROOT, "tickers", "sp500.csv")) as f:
        for row in csv.DictReader(f):
            sym = row["symbol"].replace(".", "-")  # BRK.B -> BRK-B for Yahoo
            uni[sym] = {"name": row["name"], "universe": "sp500"}
    with open(os.path.join(ROOT, "tickers", "nasdaq100.txt")) as f:
        for sym in f.read().split():
            if sym in uni:
                uni[sym]["universe"] = "both_us"  # in S&P 500 AND Nasdaq-100
            else:
                uni[sym] = {"name": sym, "universe": "nasdaq100"}
    with open(os.path.join(ROOT, "tickers", "ftse100.txt")) as f:
        for sym in f.read().split():
            uni[sym] = {"name": sym.replace(".L", ""), "universe": "ftse100"}
    return uni


def download_all(tickers):
    """Batched download; returns {ticker: OHLCV DataFrame}."""
    out = {}
    for i in range(0, len(tickers), BATCH):
        chunk = tickers[i:i + BATCH]
        data = yf.download(
            chunk, period="1y", interval="1d", group_by="ticker",
            auto_adjust=True, threads=True, progress=False,
        )
        for t in chunk:
            try:
                df = data[t].dropna(subset=["Close"]) if len(chunk) > 1 else data.dropna(subset=["Close"])
            except KeyError:
                continue
            if len(df) >= 60 and df["Close"].iloc[-1] >= MIN_PRICE:
                out[t] = df
        print(f"  downloaded {min(i + BATCH, len(tickers))}/{len(tickers)}", flush=True)
    return out


def market_regime():
    """Where each market sits vs its 200-day average (the Faber filter)."""
    try:
        idx = yf.download(["^GSPC", "^FTSE"], period="2y", interval="1d",
                          group_by="ticker", auto_adjust=True, progress=False)
        out = {}
        for key, sym, label in (("us", "^GSPC", "S&P 500"), ("uk", "^FTSE", "FTSE 100")):
            c = idx[sym]["Close"].dropna()
            if len(c) < 210:
                continue
            ma = float(c.rolling(200).mean().iloc[-1])
            out[key] = {"index": label,
                        "risk_on": bool(float(c.iloc[-1]) > ma),
                        "pct_vs_ma": round((float(c.iloc[-1]) / ma - 1) * 100, 1)}
        return out or None
    except Exception as e:
        print(f"regime fetch failed: {e}")
        return None


def main():
    uni = load_universe()
    tickers = sorted(uni)
    print(f"Universe: {len(tickers)} tickers")
    frames = download_all(tickers)
    print(f"Got usable data for {len(frames)} tickers")

    # relative strength is cross-sectional: collect 3m returns first
    setups, ret3m = [], {}
    results = {}
    for t, df in frames.items():
        hits, extras = scan_ticker(df)
        results[t] = (hits, extras, df)
        if extras["ret_3m"] is not None:
            ret3m[t] = extras["ret_3m"]

    rs_rank = pd.Series(ret3m).rank(pct=True)  # 0..1 percentile

    for t, (hits, extras, df) in results.items():
        rs = float(rs_rank.get(t, 0.5))
        # extra momentum signal: top-decile relative strength AND above SMA20
        if rs >= 0.90 and extras["close"] > df["Close"].rolling(20).mean().iloc[-1]:
            hits.append({"signal": "rel_strength", "family": "momentum",
                         "detail": f"3-month gain in the top {100 - int(rs * 100)}% of all stocks tracked"})
        if not hits:
            continue
        plan = trade_plan(df)
        if plan is None:
            continue
        setups.append({
            "ticker": t,
            "name": uni[t]["name"],
            "universe": uni[t]["universe"],
            "signals": hits,
            "families": sorted({h["family"] for h in hits}),
            "score": len(hits) + rs,          # confluence + strength
            "rs_pct": round(rs * 100),
            **{k: extras[k] for k in ("close", "chg_1d", "rsi", "spark")},
            **plan,
        })

    setups.sort(key=lambda s: -s["score"])
    # top 120 overall, but guarantee each family keeps its best 15
    top = setups[:120]
    chosen = {s["ticker"] for s in top}
    for fam in ("momentum", "mean_reversion", "vol_volume"):
        extra = [s for s in setups
                 if fam in s["families"] and s["ticker"] not in chosen][:15]
        top.extend(extra)
        chosen.update(s["ticker"] for s in extra)
    payload = {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "regime": market_regime(),
        "universe_size": len(tickers),
        "scanned": len(frames),
        "counts": {
            fam: sum(1 for s in top if fam in s["families"])
            for fam in ("momentum", "mean_reversion", "vol_volume")
        },
        "setups": top,
    }
    os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)
    with open(os.path.join(ROOT, "data", "scan.json"), "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"Wrote data/scan.json — {len(setups)} setups "
          f"({payload['counts']})")


if __name__ == "__main__":
    main()
