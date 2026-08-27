"""
Inject data/scan.json into dashboard/template.html -> dashboard/index.html.
The output is the artifact body that gets published as the hosted dashboard.
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

with open(os.path.join(ROOT, "data", "scan.json")) as f:
    scan = json.load(f)
with open(os.path.join(ROOT, "dashboard", "template.html")) as f:
    tpl = f.read()

marker = "/*__SCAN_DATA__*/"
assert marker in tpl, "placeholder missing from template"
html = tpl.replace(marker, json.dumps(scan, separators=(",", ":")))

out = os.path.join(ROOT, "dashboard", "index.html")
with open(out, "w") as f:
    f.write(html)
print(f"wrote {out} ({len(html)//1024} KB, {len(scan['setups'])} setups)")
