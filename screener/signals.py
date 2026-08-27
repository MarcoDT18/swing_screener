"""
Signal engine for the swing trade screener.

Every function takes a pandas DataFrame of daily bars for ONE ticker with
columns: Open, High, Low, Close, Volume (index = DatetimeIndex, ascending)
and returns plain numbers / booleans. scan.py wires them together.

Three signal families:
  momentum       - trend-following entries (crossovers, breakouts, rel. strength)
  mean_reversion - buying pullbacks inside an uptrend
  vol_volume     - volume spikes and volatility squeezes that precede moves
"""

import numpy as np
import pandas as pd


# ---------- basic indicators ----------

def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(100.0)  # all-gain edge case


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Wilder's Average True Range."""
    prev_close = df["Close"].shift()
    tr = pd.concat(
        [
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    sd = close.rolling(n).std(ddof=0)
    upper, lower = mid + k * sd, mid - k * sd
    bandwidth = (upper - lower) / mid
    return mid, upper, lower, bandwidth


# ---------- signal checks (each returns dict or None) ----------

def sig_ma_crossover(df, lookback=3):
    """SMA20 crossed above SMA50 within the last `lookback` sessions,
    price above both — classic fresh-uptrend entry."""
    s20, s50 = sma(df["Close"], 20), sma(df["Close"], 50)
    if len(df) < 60 or s50.iloc[-1] != s50.iloc[-1]:
        return None
    above = s20 > s50
    crossed = above & ~above.shift(1).fillna(False)
    recent = crossed.iloc[-lookback:]
    if recent.any() and df["Close"].iloc[-1] > s20.iloc[-1]:
        days_ago = lookback - 1 - int(np.argmax(recent.values[::-1]))
        return {"signal": "ma_crossover", "family": "momentum",
                "detail": f"20-day average rose above the 50-day {days_ago} day(s) ago"}
    return None


def sig_breakout(df, window=55, vol_mult=1.5):
    """Close at a new `window`-day high on above-average volume."""
    if len(df) < window + 5:
        return None
    prior_high = df["Close"].iloc[-(window + 1):-1].max()
    vol_avg = df["Volume"].iloc[-21:-1].mean()
    last = df.iloc[-1]
    if last["Close"] > prior_high and last["Volume"] > vol_mult * vol_avg:
        return {"signal": "breakout", "family": "momentum",
                "detail": f"highest close in {window} days, on {last['Volume']/vol_avg:.1f}x normal volume"}
    return None


def rel_strength_3m(df):
    """3-month return, used cross-sectionally for the RS percentile."""
    if len(df) < 64:
        return None
    return float(df["Close"].iloc[-1] / df["Close"].iloc[-63] - 1)


def sig_pullback(df):
    """Mean reversion: established uptrend (close > SMA150, SMA50 rising),
    RSI(14) dipped below 40, price within 3% of SMA50 — buying the dip
    at a logical support, not catching a falling knife."""
    if len(df) < 160:
        return None
    close = df["Close"]
    s50, s150 = sma(close, 50), sma(close, 150)
    r = rsi(close).iloc[-1]
    uptrend = close.iloc[-1] > s150.iloc[-1] and s50.iloc[-1] > s50.iloc[-11]
    near_support = abs(close.iloc[-1] / s50.iloc[-1] - 1) < 0.03
    if uptrend and r < 40 and near_support:
        return {"signal": "pullback", "family": "mean_reversion",
                "detail": f"long-term climb intact, now resting at its 50-day support (RSI {r:.0f})"}
    return None


def sig_oversold_bounce(df):
    """Mean reversion: close below lower Bollinger band while the 50-day
    trend still points up, and today closed green (first sign of a turn)."""
    if len(df) < 60:
        return None
    close = df["Close"]
    _, _, lower, _ = bollinger(close)
    s50 = sma(close, 50)
    green = close.iloc[-1] > df["Open"].iloc[-1]
    if close.iloc[-2] < lower.iloc[-2] and s50.iloc[-1] > s50.iloc[-11] and green:
        return {"signal": "oversold_bounce", "family": "mean_reversion",
                "detail": "fell below its usual range, then closed higher on the day"}
    return None


def sig_volume_spike(df, mult=2.5):
    """Volume > `mult`x its 20-day average on an up day — institutional
    footprints often precede continuation."""
    if len(df) < 25:
        return None
    vol_avg = df["Volume"].iloc[-21:-1].mean()
    last, prev = df.iloc[-1], df.iloc[-2]
    if vol_avg > 0 and last["Volume"] > mult * vol_avg and last["Close"] > prev["Close"]:
        return {"signal": "volume_spike", "family": "vol_volume",
                "detail": f"{last['Volume']/vol_avg:.1f}x normal volume on a rising day"}
    return None


def sig_squeeze(df, pctile=0.10):
    """Volatility contraction: 20-day Bollinger bandwidth in the lowest
    decile of the past 6 months. Quiet coils tend to resolve violently;
    direction bias from position vs SMA50."""
    if len(df) < 150:
        return None
    close = df["Close"]
    _, _, _, bw = bollinger(close)
    hist = bw.iloc[-126:].dropna()
    if len(hist) < 60:
        return None
    if bw.iloc[-1] <= hist.quantile(pctile) and close.iloc[-1] > sma(close, 50).iloc[-1]:
        return {"signal": "squeeze", "family": "vol_volume",
                "detail": "quietest trading range in six months, still above its 50-day trend"}
    return None


ALL_CHECKS = [sig_ma_crossover, sig_breakout, sig_pullback,
              sig_oversold_bounce, sig_volume_spike, sig_squeeze]


# ---------- trade plan ----------

def trade_plan(df, atr_stop=1.5, atr_target=2.5):
    """Simple ATR-based plan: entry at last close, stop 1.5*ATR below,
    target 2.5*ATR above -> reward:risk ~1.67 before slippage."""
    a = atr(df).iloc[-1]
    entry = float(df["Close"].iloc[-1])
    if a != a or a <= 0:
        return None
    stop = entry - atr_stop * a
    target = entry + atr_target * a
    return {
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target": round(target, 2),
        "atr": round(float(a), 2),
        "rr": round((target - entry) / (entry - stop), 2),
    }


def scan_ticker(df):
    """Run every check on one ticker. Returns (hits, extras)."""
    hits = [h for h in (chk(df) for chk in ALL_CHECKS) if h]
    extras = {
        "close": round(float(df["Close"].iloc[-1]), 2),
        "chg_1d": round(float(df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100, 2)
        if len(df) > 1 else 0.0,
        "rsi": round(float(rsi(df["Close"]).iloc[-1]), 1) if len(df) > 20 else None,
        "ret_3m": rel_strength_3m(df),
        "spark": [round(float(x), 4) for x in df["Close"].iloc[-60:].tolist()],
    }
    return hits, extras
