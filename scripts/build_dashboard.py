#!/usr/bin/env python3
"""Build the B2B dashboard HTML from a data JSON file.

Usage: python3 scripts/build_dashboard.py data/latest.json out/B2B_Dashboard.html

data JSON shape: {"D": {...}, "cohort": [...], "NA": {...}}  (see data/sample_data.json and RUNBOOK.md)
D.meta must contain: asof (YYYY-MM-DD), range (e.g. "Oct 31 – Nov 6"), utc_range (e.g. "Oct 31 – Nov 7 UTC").
"""
import json, sys, pathlib
root = pathlib.Path(__file__).resolve().parent.parent
src, dst = sys.argv[1], sys.argv[2]
data = json.load(open(src))
D, cohort, NA = data["D"], data["cohort"], data["NA"]
meta = D["meta"]
assert len(cohort) == len(D["top"]), "cohort and top must have one entry per day"
assert len(D["enr"]) >= 1 and len(D["flows"]) >= 1
html = (root / "template" / "dashboard_template.html").read_text()
html = html.replace("__D__", json.dumps(D)).replace("__COHORT__", json.dumps(cohort)).replace("__NA__", json.dumps(NA))
html = html.replace("Oct 31 – Nov 5 UTC", meta["utc_range"])
html = html.replace("as of Nov 4 2026", "as of " + meta["asof_long"] if "asof_long" in meta else "as of " + meta["asof"])
html = html.replace("Oct 31 – Nov 4", meta["range"])
pathlib.Path(dst).parent.mkdir(parents=True, exist_ok=True)
pathlib.Path(dst).write_text(html)
print("wrote", dst, len(html), "bytes;", len(D["leads"]), "leads;", len(D["top"]), "days")
