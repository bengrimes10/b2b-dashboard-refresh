# B2B dashboard refresh kit

Rebuilds the interactive B2B flow / sales dashboard (single self-contained HTML file) from fresh Klaviyo and BigQuery data.

**Current B2B sales definition:** every US Shopify order must have at least $3,000 in BigQuery net sales. All purchasers meeting that threshold count, even without a B2B signal. Lists, segments, flow emails, forms, and coupons describe those orders; they never add an order below $3,000. Snapshots created before this correction used a broader definition and should not be compared directly.

- `template/dashboard_template.html`: dashboard with three data placeholders (`__D__`, `__COHORT__`, `__NA__`).
- `scripts/build_dashboard.py`: fills the template from a data JSON and writes the HTML.
- `data/sample_data.json`: example data (no customer records; `leads` is empty).
- `RUNBOOK.md`: the exact daily procedure and queries.

No customer emails, names or phone numbers are stored in this repo. Warm-lead rows are pulled from BigQuery on each run and exist only in the generated HTML.

## Codex local build

The repository is checked out at `C:\Codex\My First Project\b2b-dashboard-refresh`. The generated HTML, customer-level input, screenshots, and signal cache stay in git-ignored `out/` and `data/cache/`. The metrics-only history file omits `D.leads` and `D.big`.

1. Install `requirements.txt` in a Python environment and install Chromium with `playwright install chromium`.
2. Sign in to Google Application Default Credentials (`gcloud auth application-default login`). The account needs read access to `hd-global-analytics-platform.mart_ecom_tracker` and permission to run EU BigQuery jobs. Billing project defaults to `db-project-30840` or can be set with `BIGQUERY_PROJECT_ID`.
3. In Klaviyo, open **Settings → API keys → Create Private API Key**. Choose **Read-Only Key** and name it `Codex B2B dashboard`. An Owner, Admin, or Manager must create it. Run `python scripts/setup_klaviyo_key.py` in a private terminal and paste the key **once** at the hidden prompt. When it says the key was saved, press Enter to close the window. It is saved in Windows Credential Manager for your Windows account. Never paste it into chat or commit it. The required read scopes are profiles, lists, segments, flows, campaigns, events, metrics, coupons, and coupon codes. The `KLAVIYO_PRIVATE_API_KEY` environment variable is also supported.
4. Run `python scripts/refresh_dashboard.py --checkpoint-only` to compare the historical sales checkpoint. Then run `python scripts/refresh_dashboard.py` and `python scripts/verify_dashboard.py out/B2B_Dashboard.html`.

The script currently flags attribution as unavailable because the prior weighted K-SQL attribution is not reproduced by the public API pull. It shows assigned coupons and matching order codes; the Klaviyo Used count is shown as unknown. A verified November 8 late-sales adjustment is documented in `docs/validation_2026-11-09.md`; other checkpoint changes over 1% stop publication. **Keep the Codex schedule paused and the older agent's task running until the owner reviews the private validation dashboard and chooses the cutover.**

The first run reads flow-email and form events back to November 2023. Later runs use the local ignored signal cache with a six-hour overlap. The read-only REST path passed a live end-to-end validation on November 9, 2026; the remaining attribution and cutover limits are documented above.

For a historical layout preview that contains no customer rows:

`python scripts/build_dashboard.py data/history/2026-11-09.json out/B2B_Dashboard_preview.html`
