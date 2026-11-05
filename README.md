# B2B dashboard refresh kit

Rebuilds the interactive B2B flow / sales dashboard (single self-contained HTML file) from fresh Klaviyo and BigQuery data.

- `template/dashboard_template.html`: dashboard with three data placeholders (`__D__`, `__COHORT__`, `__NA__`).
- `scripts/build_dashboard.py`: fills the template from a data JSON and writes the HTML.
- `data/sample_data.json`: example data (no customer records; `leads` is empty).
- `RUNBOOK.md`: the exact daily procedure and queries.

No customer emails, names or phone numbers are stored in this repo. Warm-lead rows are pulled from BigQuery on each run and exist only in the generated HTML.
