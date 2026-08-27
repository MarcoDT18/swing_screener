"""
Inject data/scan.json into dashboard/template.html and wrap it into a
complete standalone page at site/index.html — ready for GitHub Pages.
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
body = tpl.replace(marker, json.dumps(scan, separators=(",", ":")))
# the template's first line is a <title> tag meant for artifact publishing;
# move that concern into the proper <head> here
body = body.replace("<title>Signal Desk</title>\n", "", 1)

FAVICON = ("data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 "
           "viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>"
           "%F0%9F%93%88</text></svg>")

page = (
    "<!doctype html>\n<html lang=\"en\">\n<head>\n"
    "<meta charset=\"utf-8\">\n"
    "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
    "<title>Signal Desk</title>\n"
    f"<link rel=\"icon\" href=\"{FAVICON}\">\n"
    "</head>\n<body>\n" + body + "\n</body>\n</html>\n"
)

os.makedirs(os.path.join(ROOT, "site"), exist_ok=True)
out = os.path.join(ROOT, "site", "index.html")
with open(out, "w") as f:
    f.write(page)
print(f"wrote {out} ({len(page)//1024} KB, {len(scan['setups'])} setups)")
