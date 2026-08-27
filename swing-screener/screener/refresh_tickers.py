"""
Refresh index constituent lists from Wikipedia (runs in CI where the
network is open). Best-effort: on any failure the committed lists stay.
"""

import csv
import os

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADERS = {"User-Agent": "Mozilla/5.0 (swing-screener; personal project)"}


def read_tables(url):
    import urllib.request
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return pd.read_html(r.read())


def refresh_sp500():
    tables = read_tables("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies")
    df = tables[0]
    with open(os.path.join(ROOT, "tickers", "sp500.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "name", "sector"])
        for _, r in df.iterrows():
            w.writerow([r["Symbol"], r["Security"], r.get("GICS Sector", "")])
    print(f"sp500: {len(df)} rows")


def refresh_nasdaq100():
    tables = read_tables("https://en.wikipedia.org/wiki/Nasdaq-100")
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        if any("ticker" in c or "symbol" in c for c in cols) and len(t) > 80:
            col = t.columns[[i for i, c in enumerate(cols) if "ticker" in c or "symbol" in c][0]]
            syms = [str(s).strip() for s in t[col] if str(s).strip()]
            with open(os.path.join(ROOT, "tickers", "nasdaq100.txt"), "w") as f:
                f.write(" ".join(syms) + "\n")
            print(f"nasdaq100: {len(syms)} symbols")
            return
    raise RuntimeError("Nasdaq-100 table not found")


def refresh_ftse100():
    tables = read_tables("https://en.wikipedia.org/wiki/FTSE_100_Index")
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        if any("ticker" in c or "epic" in c for c in cols) and len(t) > 80:
            col = t.columns[[i for i, c in enumerate(cols) if "ticker" in c or "epic" in c][0]]
            syms = []
            for s in t[col]:
                s = str(s).strip().replace(".", "-")   # BT.A -> BT-A
                if s and s.lower() != "nan":
                    syms.append(s + ".L")              # Yahoo suffix for LSE
            with open(os.path.join(ROOT, "tickers", "ftse100.txt"), "w") as f:
                f.write(" ".join(syms) + "\n")
            print(f"ftse100: {len(syms)} symbols")
            return
    raise RuntimeError("FTSE 100 table not found")


if __name__ == "__main__":
    for fn in (refresh_sp500, refresh_nasdaq100, refresh_ftse100):
        try:
            fn()
        except Exception as e:  # keep committed list on any failure
            print(f"{fn.__name__} failed: {e}")
