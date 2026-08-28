"""
Build the whole site into site/:
  index.html    — the daily dashboard, from data/scan.json
  backtest.html — the backtest results, from data/backtest.json (if present)
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAVICON = ("data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 "
           "viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>"
           "%F0%9F%93%88</text></svg>")


def wrap(body, title):
    body = body.replace(f"<title>{title}</title>\n", "", 1)
    return (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\">\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
        f"<title>{title}</title>\n"
        f"<link rel=\"icon\" href=\"{FAVICON}\">\n"
        "</head>\n<body>\n" + body + "\n</body>\n</html>\n"
    )


def build(template, marker, data_file, out_name, title):
    with open(os.path.join(ROOT, "dashboard", template)) as f:
        tpl = f.read()
    assert marker in tpl, f"{marker} missing from {template}"
    path = os.path.join(ROOT, "data", data_file)
    payload = "null"
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
        # the backtest page doesn't need the raw trade lists (Monte Carlo does)
        for sv in data.get("walkforward", {}).get("signals", {}).values():
            sv.pop("oos_r", None)
        payload = json.dumps(data, separators=(",", ":"))
    html = wrap(tpl.replace(marker, payload), title)
    os.makedirs(os.path.join(ROOT, "site"), exist_ok=True)
    out = os.path.join(ROOT, "site", out_name)
    with open(out, "w") as f:
        f.write(html)
    print(f"wrote {out} ({len(html)//1024} KB)")


if __name__ == "__main__":
    build("template.html", "/*__SCAN_DATA__*/", "scan.json",
          "index.html", "Signal Desk")
    build("backtest_template.html", "/*__BT_DATA__*/", "backtest.json",
          "backtest.html", "Signal Desk — Backtest")
    build("strategy_template.html", "/*__MC_DATA__*/", "montecarlo.json",
          "strategy.html", "Signal Desk — Strategy")
