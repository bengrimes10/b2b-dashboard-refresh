# Codex validation — 2026-11-09

**Historical definition notice (2026-11-09):** The B2B cohort figures in this validation were produced before the user's final correction. They include signal-linked orders below $3,000 and are superseded by `docs/threshold_correction_2026-11-09.md`. The US sales checkpoint and API/rendering findings below remain valid.

The connected Klaviyo account and new read-only key both identify SelectBlinds (account `UzEnEn`). Google Application Default Credentials can run read-only jobs in the EU BigQuery dataset. Customer records remain only in ignored local files under `out/` and `data/cache/`.

## Source and rendering checks

- Four live trade/B2B flows and five flow messages were returned by the reporting API. They had 5,856 recipients through the run; the four B2B campaign sends had 7,746 recipients.
- The public event endpoint supplied current audience memberships and the flow/form history beginning November 2023. Positive US Shopify order events: 6,187; orders with at least one qualifying Klaviyo signal: 339. BigQuery matched 335 core or extended-only orders, including large orders added directly from BigQuery.
- The nine complete sales days (October 31 through November 8) sum to $2,543,806.36 US net sales. The core B2B cohort sums to $382,268.66 on 217 orders, or 15.0% of US net sales. The historical snapshot has $375,953.76 on 211 orders. Four additional core orders appear on November 6 and two on November 7 under the refreshed current-membership and historical-signal pull; the business definition intentionally uses current membership, so old days can be reclassified. The November 6 and 7 core net-sales changes are +8.43% and +10.19% respectively.
- The large-order SQL alone reproduced the saved checkpoint exactly: 55 orders and $251,188.74 at or above $3,000 net sales.
- The private HTML contains 400 warm leads and 212 ranked B2B orders. Readable qualifying details are present for 174; the other 38 are large-order-only. Chromium rendered all four tabs with no console errors and exercised the ranked-order expansion.

## November 8 US-sales checkpoint

The saved snapshot has $300,228.94. BigQuery time travel on `net_sales_bookings` at 2026-11-09 04:00 America/Phoenix reproduced that amount exactly. The current table returns $287,641.32, a $12,587.62 or 4.19% reduction. Its components changed as follows (USD):

| Component | Earlier table | Current table | Change |
| --- | ---: | ---: | ---: |
| Gross sales | 626,968.40 | 628,855.53 | +1,887.13 |
| Discounts | 272,239.65 | 272,435.63 | +195.98 |
| Cancellations | -31,358.13 | -45,580.12 | -14,221.99 |
| Taxes | 23,141.68 | 23,198.46 | +56.78 |
| Net sales | 300,228.94 | 287,641.32 | -12,587.62 |

This verifies a later table adjustment, chiefly increased cancellations. `scripts/refresh_dashboard.py` accepts this specific verified baseline and still flags any further change beyond 1%.

## Open limits before daily cutover

- Klaviyo order-level weighted attribution from the prior K-SQL report is not reproduced by the local public-API pipeline. The dashboard labels the attribution section unavailable.
- TRADE15 and TRADE20 each have zero unassigned codes. TRADE10's unassigned-code lookup timed out and remains unknown. The script reports these states without inventing a count. Klaviyo's coupon Used status is not treated as redemption.
- The daily Codex schedule remains paused. The previous agent's task should remain in place until the owner reviews the new dashboard and chooses the cutover.
