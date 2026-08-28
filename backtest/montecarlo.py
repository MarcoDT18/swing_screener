"""
Monte Carlo strategy simulator.

Takes the walk-forward backtest's OUT-OF-SAMPLE trades (data/backtest.json)
for the signals that showed a real edge, and resamples them into thousands
of simulated trading years to answer the questions a backtest can't:

  * At a given risk-per-trade, what does a normal / lucky / ugly year look like?
  * How deep a drawdown should be EXPECTED even if the edge is real?
  * At what position size does the strategy start destroying itself?

Method: each simulated year draws N trades (with replacement) from the pooled
out-of-sample R distribution and compounds equity trade by trade at risk
fraction f: equity *= 1 + f * R. Repeated 10,000 times per sizing scenario,
plus a stress run with every trade's R docked 0.05 (extra slippage).
A grid-searched Kelly fraction is reported for reference — with the standard
warning that full Kelly is violently volatile and half/quarter Kelly is what
practitioners actually use.

Reads  data/backtest.json  → writes data/montecarlo.json.
Run: python backtest/montecarlo.py
"""

import json
import os
import datetime as dt

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# a signal joins the tradable pool if its out-of-sample edge and p clear these
MIN_EDGE, MAX_P = 0.02, 0.20
RISK_GRID = [0.0025, 0.005, 0.01, 0.02]     # risk per trade as fraction of equity
N_SIMS = 10_000
MAX_TRADES_PER_YEAR = 250                   # practical cap for one person
STRESS_HAIRCUT = 0.05                       # extra slippage, in R
RNG = np.random.default_rng(7)


def simulate_years(pool, trades_per_year, f, n_sims=N_SIMS):
    """Vectorized: n_sims years of `trades_per_year` sequential trades."""
    draws = RNG.choice(pool, size=(n_sims, trades_per_year), replace=True)
    growth = 1.0 + f * draws
    growth = np.maximum(growth, 0.01)                    # floor: a trade can't take equity below 0
    equity = np.cumprod(growth, axis=1)
    final = equity[:, -1]
    running_max = np.maximum.accumulate(np.hstack([np.ones((n_sims, 1)), equity]), axis=1)
    dd = 1.0 - equity / running_max[:, 1:]
    max_dd = dd.max(axis=1)
    return final - 1.0, max_dd


def scenario_stats(annual, max_dd):
    return {
        "median_annual_pct": round(float(np.median(annual)) * 100, 1),
        "p05_annual_pct": round(float(np.percentile(annual, 5)) * 100, 1),
        "p95_annual_pct": round(float(np.percentile(annual, 95)) * 100, 1),
        "p_losing_year_pct": round(float((annual < 0).mean()) * 100, 1),
        "median_max_dd_pct": round(float(np.median(max_dd)) * 100, 1),
        "p95_max_dd_pct": round(float(np.percentile(max_dd, 95)) * 100, 1),
        "p_dd_over_20_pct": round(float((max_dd > 0.20).mean()) * 100, 1),
    }


def kelly_fraction(pool):
    """Grid-search f maximizing E[log(1 + f*R)]."""
    fs = np.linspace(0.001, 0.20, 400)
    best_f, best_g = 0.0, -np.inf
    for f in fs:
        g = np.log(np.maximum(1 + f * pool, 1e-6)).mean()
        if g > best_g:
            best_f, best_g = float(f), float(g)
    return best_f


def main():
    with open(os.path.join(ROOT, "data", "backtest.json")) as f:
        bt = json.load(f)
    wf = bt.get("walkforward", {}).get("signals", {})

    pool, members, rate = [], [], 0.0
    for sig, v in wf.items():
        if "oos_r" not in v:
            continue
        edge = v["test"]["avg_r"] - v["test_baseline_avg_r"]
        p = v.get("p_test")
        if edge >= MIN_EDGE and p is not None and p <= MAX_P:
            pool.extend(v["oos_r"])
            months = max(v.get("oos_months") or 12, 1)
            rate += len(v["oos_r"]) / months * 12
            members.append({"signal": sig, "oos_edge": round(edge, 3),
                            "p_test": p, "hold": v["chosen_hold"],
                            "trades": len(v["oos_r"])})
    if not pool:
        print("No signal qualified for the pool — nothing to simulate.")
        payload = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                   "pool": None}
    else:
        pool = np.array(pool, dtype=float)
        trades_per_year = int(min(MAX_TRADES_PER_YEAR, rate))
        scenarios, stress = [], []
        for f in RISK_GRID:
            a, d = simulate_years(pool, trades_per_year, f)
            scenarios.append({"risk_pct": f * 100, **scenario_stats(a, d)})
            a2, d2 = simulate_years(pool - STRESS_HAIRCUT, trades_per_year, f)
            stress.append({"risk_pct": f * 100, **scenario_stats(a2, d2)})
        kf = kelly_fraction(pool)
        # distribution for the chart, at the recommended (quarter-Kelly-ish) size
        rec = min(min(RISK_GRID, key=lambda x: abs(x - kf / 4)), 0.01)  # never suggest above 1%
        a, _ = simulate_years(pool, trades_per_year, rec)
        hist_counts, hist_edges = np.histogram(a * 100, bins=40)
        payload = {
            "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "criteria": {"min_oos_edge": MIN_EDGE, "max_p": MAX_P},
            "pool": {"signals": members, "n_trades": int(len(pool)),
                     "avg_r": round(float(pool.mean()), 3),
                     "trades_per_year_used": trades_per_year},
            "kelly": {"full_pct": round(kf * 100, 2),
                      "quarter_pct": round(kf / 4 * 100, 2),
                      "recommended_risk_pct": rec * 100},
            "sims_per_scenario": N_SIMS,
            "scenarios": scenarios,
            "stress": {"haircut_r": STRESS_HAIRCUT, "scenarios": stress},
            "histogram": {"risk_pct": rec * 100,
                          "counts": [int(c) for c in hist_counts],
                          "edges_pct": [round(float(e), 1) for e in hist_edges]},
        }
    with open(os.path.join(ROOT, "data", "montecarlo.json"), "w") as f:
        json.dump(payload, f, indent=1)
    print(json.dumps(payload.get("pool"), indent=1))
    if payload.get("pool"):
        print("kelly:", payload["kelly"])
        for s in payload["scenarios"]:
            print(f"risk {s['risk_pct']:>5}%: median {s['median_annual_pct']:+.1f}%/yr "
                  f"[{s['p05_annual_pct']:+.1f} … {s['p95_annual_pct']:+.1f}]  "
                  f"losing-year {s['p_losing_year_pct']}%  medDD {s['median_max_dd_pct']}%")


if __name__ == "__main__":
    main()
