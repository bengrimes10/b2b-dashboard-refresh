# B2B sales threshold correction — 2026-11-09

The owner clarified that **every** qualifying B2B sale must be a US Shopify order with BigQuery net sales of at least $3,000. Every purchaser meeting that minimum counts regardless of channel, list, flow, form, or coupon. These signals describe only threshold orders; they cannot add an order below $3,000. CRTRA10 remains excluded as a coupon signal.

| Complete sales dates | $3,000+ orders | B2B net sales | Share of US net | With a core B2B signal | Without a core B2B signal |
| --- | ---: | ---: | ---: | ---: | ---: |
| October 1–31, 2026 | 155 | $667,878.29 | 8.5% | 38 orders / $179,401.15 | 117 orders / $488,477.14 |
| October 31–November 8, 2026 | 55 | $251,188.74 | 9.9% | 17 orders / $105,004.17 | 38 orders / $146,184.57 |

October 31 appears in both requested views, so the two rows must not be added. The main dashboard's October 31–November 8 cohort reproduces the independent large-order SQL checkpoint of 55 orders and $251,188.74. Its `core` count equals its `$3,000+` count; `ext` is zero. The minimum included order net is $3,024.74 in the nine-day view and $3,002.18 for October. Both private HTML reports were checked in Chromium. They remain under ignored `out/`; no customer details are in this note or the metrics-only corrected snapshot.

The earlier growth readout used the broader B2B definition and is superseded. Under the corrected definition, October 1–30 averaged $21,271.30 B2B net sales per day; October 31–November 8 averaged $27,909.86, up 31.2%. Share of US net sales rose from 8.38% to 9.87%. The immediately preceding nine days, October 22–30, had $216,664.24 versus $251,188.74 in the following nine days, up 15.9%. These comparisons show an increase in observed $3,000+ sales, but do not establish that the more active approach caused it. Current list/segment membership and ever-received flow/form signals may postdate an order. Klaviyo email conversion reports include all order sizes and remain context, not cohort attribution.

The 2026-11-09 original history snapshot uses the older definition. The corrected metrics-only baseline is `data/history/2026-11-09-threshold-corrected.json` and must be used for later B2B comparisons.
