# Daily refresh runbook (for the scheduled agent)

Goal: rebuild `B2B_Dashboard.html` with data from **2026-10-31 through today**, send it to the user with SendUserFile (display "render"), and commit a metrics-only snapshot (no leads) to `data/history/YYYY-MM-DD.json`.
Timezone for "today": America/Phoenix. All Klaviyo model calls need `model="claude-sonnet-5-5"` (or your model). Read-only on Klaviyo and BigQuery; never create/update/delete anything there.

## 0. Setup
1. `git clone` this repo (use add_repo with push access if needed). Work in a scratch dir.
2. Load Klaviyo tool schemas with ToolSearch (get_flows, get_flow_report, get_campaign_report, get_campaigns, ask_analytics_question, get_coupon_codes) and BigQuery `execute_sql_readonly`.
3. BigQuery project: `hd-global-analytics-platform`. Primary tables: `mart_ecom_tracker.net_sales_bookings`, `mart_ecom_tracker.order_items`. Follow the ecom-metrics dictionary skill if available: samples excluded (`order_type='Order'`), net sales from net_sales_bookings, units/orders/cost from order_items, margin = PRODUCT margin using `estimated_cost_usd` (invoice `cost_usd` is sparsely populated), US only (Shopify datasource for order-name joins).

## 1. Live B2B flows (Flows page)
- `get_flows` with `contains(name,"Trade")`, `"Commercial"`, `"B2B"` (fields name,status). Live = status `live`. Flows in scope today: XA5MGj, SZMWtA, SVNQ6Y, RjvnXL (re-check daily for new live flows).
- `get_flow_report` (conversion metric Placed Order Shopify = `WvaquY`), group_by flow_id, flow_message_id, flow_message_name, timeframe 2026-10-31T00:00-07:00 to now. Statistics: recipients, delivered, opens_unique, clicks_unique, conversion_uniques, unsubscribe_uniques, bounced, spam_complaints; value: conversion_value. Fill `D.msgs[]` {f, m, rec, del, opens, clicks, conv, rev, unsub, bounced, spam}. A flow with no row has zero sends: still add a zero row.
- Flow structure (`get_flow` with additional_fields definition): re-check branches for dead ends (else branch with `links: null`), branch conditions vs labels, single-email flows, coupon tags in templates (search template HTML for `{% coupon_code 'NAME' %}`). Rewrite `D.issues[]` {sev HIGH|MEDIUM|INFO, flow, issue, evidence, action}. Drop issues that are fixed; add new ones. Keep severity honest.

## 2. New enrollees (by day)
- Metric "Received Email" `YiWaij`, `query_metric_aggregates`, by `$flow`, interval day, timezone US/Arizona, measurement unique, filter to the live flow ids. Fill `D.enr[]` {d: "YYYY-MM-DD", <flowId>: n ...} for every day from 2026-10-31 (zeros where none).

## 3. B2B cohort, sales, margin (B2B sales page)
Cohort = US buyers who placed a Shopify order AND (a) are currently in a core list, OR (b) in a core segment, OR (c) ever received an email from any Trade/Commercial/B2B flow (live or draft), OR (d) submitted a trade/B2B form, OR (e) used a trade/B2B-style discount code (a code containing trade, trd or b2b, e.g. 'Trade Discount', 'SBB2B10', or Klaviyo unique codes like TRADE15-TRADE15XXXXXXXX).
- Core lists: RArqFN, SuJXq5, XYuXP5, S3uABq, TM29XF, UeKxx2, UwUddt
- Core segments: UtP84e, U2Z8YK, VExknn, Ycbxb4, R77LxD, Yb9xBp, UMxDa4
- Flows (15): XA5MGj SZMWtA SVNQ6Y RjvnXL QTXBNv R77Xza T8tDXU TgGSgX ThRrHh XV5Jxt XcCt2k Y89wS2 YiK4az SWgfrf YnCsYM
- Forms (8): Uxsw3c VpZjF6 Rjbv4E Sx2BJq U5HrsT Yewkfv VBYJhK VDnyVy
- Coupon: discount code containing trade, trd or b2b (case-insensitive). CRTRA10 is deliberately EXCLUDED until the user confirms what it is (it appears on ~93 orders / $43K per 5 days, mostly from buyers who are otherwise not in the cohort). Keep reporting its volume in D.issues.
- Extended (separate toggle): lists Rh4564, VGrNiu; segments SmZ4HF, XsSRLS
Re-scan lists/segments occasionally for new trade/commercial/B2B names (get_lists / get_segments, paged 10 at a time).

Step A (Klaviyo): `ask_analytics_question` (K-SQL) asking for Shopify Placed Order events (`klaviyo.events.shopify.Order`) from 2026-10-31 UTC to tomorrow, one row per order, with columns order_name (`extra.name`), order_value, discount_codes, and 0/1 flags in_core_list, in_core_segment, got_flow_email, submitted_form, used_coupon, in_extended; only orders with any flag. Ask for ONE ROW PER ORDER (order_name, flag_string) up to 1000 rows. K-SQL does NOT support STRING_AGG, so aggregated name lists fail; plain rows work (about 190 rows). After each K-SQL call, READ the SQL it returns and verify every list/segment/flow/form ID is exactly as given in this runbook: the tool is LLM-written and has mistyped an ID before (SZMWtA became SZWtA once). Known behavior: list/segment tables are `klaviyo.relations.list_memberships` / `segment_memberships` (current membership); flow emails `klaviyo.events.received_emails` (data.$flow); forms `klaviyo.events.api.\`Form submitted by profile\`` (data.form_id); orders join on `profile_internal_id`. Note the K-SQL tool times out at 60s: keep queries simple; run pieces separately if needed.
Encode each order as `<order_name><flags>` with letters l=list s=segment f=flow-email m=form c=coupon x=extended, e.g. `US126391lfmx`.

Step B (BigQuery): join those order names to BigQuery. Template:
```sql
WITH raw AS (SELECT x FROM UNNEST(SPLIT('<space-separated encoded orders>', ' ')) x),
c AS (SELECT SUBSTR(x,1,8) order_name, SUBSTR(x,9) fl FROM raw WHERE x != ''),   -- adjust SUBSTR if order names are longer than 8 chars
c2 AS (SELECT order_name, REGEXP_CONTAINS(fl,r'[lsfmc]') core, REGEXP_CONTAINS(fl,r'l') f_list, REGEXP_CONTAINS(fl,r's') f_seg, REGEXP_CONTAINS(fl,r'f') f_flow, REGEXP_CONTAINS(fl,r'm') f_form, REGEXP_CONTAINS(fl,r'c') f_coupon FROM c),
oi AS (SELECT order_name, MIN(order_created_at) d, SUM(net_revenue_usd) oi_net, SUM(estimated_cost_usd) est_cost FROM `hd-global-analytics-platform.mart_ecom_tracker.order_items` WHERE country='US' AND order_type='Order' AND DataSource='Shopify' AND order_created_at BETWEEN '2026-10-31' AND CURRENT_DATE() AND order_name IN (SELECT order_name FROM c2) GROUP BY 1),
nb AS (SELECT order_name, SUM(net_sales_usd) net_sales FROM `hd-global-analytics-platform.mart_ecom_tracker.net_sales_bookings` WHERE country='US' AND date_ref BETWEEN '2026-10-31' AND CURRENT_DATE() AND order_name IN (SELECT order_name FROM c2) GROUP BY 1)
SELECT FORMAT_DATE('%Y-%m-%d', oi.d) d, IF(c2.core,'core','ext_only') tier, COUNT(*) orders, SUM(nb.net_sales) net_sales, SUM(oi.est_cost) est_cost,
 COUNTIF(f_list) n_list, COUNTIF(f_seg) n_seg, COUNTIF(f_flow) n_flow, COUNTIF(f_form) n_form, COUNTIF(f_coupon) n_coupon,
 SUM(IF(f_list,nb.net_sales,0)) s_list, SUM(IF(f_seg,nb.net_sales,0)) s_seg, SUM(IF(f_flow,nb.net_sales,0)) s_flow, SUM(IF(f_form,nb.net_sales,0)) s_form, SUM(IF(f_coupon,nb.net_sales,0)) s_coupon
FROM c2 JOIN oi USING(order_name) LEFT JOIN nb USING(order_name) GROUP BY 1,2 ORDER BY 1,2
```
Fill `cohort[]` (one per day, same order as `D.top`): `{d:'MM-DD', core:{o,net,cost,nl,ns,nf,nm,nc,sl,ss,sf,sm,sc}, ext:{o,net,cost}}` (core = tier core; ext = tier ext_only; zero-fill days with none).

US totals per day -> `D.top[]` (alias the order_items columns distinctly, e.g. `oi.oi_net`, `oi.est_cost`, to avoid nested-record output) {d:'YYYY-MM-DD', net (net_sales_bookings net_sales_usd), gross (gross_sales_usd), orders (distinct order key: order_name if Shopify else order_id, order_type='Order', is_original_version IS NULL OR =1), oi (order_items SUM(net_revenue_usd)), cost (SUM(estimated_cost_usd))}.
Disclose in the dashboard text: samples excluded; net sales = total net revenue incl. shipping/other charges; cost = PRODUCT cost only, estimated cost covers only about a third of lines.

## 4. Attribution, campaigns, coupons (B2B sales page drill-downs) -> `NA`
- Attribution: K-SQL on `klaviyo.attribution.message` (join on order event id; fields flow_id, campaign_id, message_type) for cohort orders (lists + segments + flow-email cohort, value > 0). Group by flow_id / campaign_id with orders and value, plus an unattributed row and total. Map flow ids to names via `get_flows` (filter any(id,[...])); map campaign ids to names via `get_campaigns` (the id filter may return a page of campaigns; look up the ids in the result with jq/python). Mark `b2b:true` for flows whose name contains Trade/Commercial/B2B and campaigns named [Commercial]/B2B/Trade. The attribution K-SQL may return attribution-WEIGHTED (fractional) order counts; that is fine. Fill `NA.attr` {none:{o,v}, total:{o,v}, flows:[{n,id,o,v,b2b}], camps:[{n,id,o,v,b2b}]}.
- B2B campaigns: `get_campaign_report` (conversion metric `WvaquY`, detail_filters name contains-any ["Commercial","B2B","Trade"], timeframe 2026-10-31 to now) -> `NA.camp[]` {n, aud, sent, rec, dl, opens, clicks, conv, rev, unsub}. `NA.sends` = {flows: total live-flow recipients, camps: total B2B campaign recipients}.
- Coupons: K-SQL on `klaviyo.events.assigned_coupons` (data.CouponKey, data.UniqueCode) for CouponKey in (TRADE10, TRADE15, TRADE20, Trade_TRD, Trade_TRA, TRD500_Email) or starting with B2B -> assigned counts since 2026-10-31; match UniqueCode (case-insensitive) against Shopify order discount codes for redemption; `klaviyo.events.used_coupons` currently shows 0 (not syncing), so state that. Codes left: unique codes look like `TRADE15-TRADE15XXXXXXXX`; `get_coupon_codes` filter `and(equals(coupon.id,"X"),equals(status,"UNASSIGNED"))`, page_size 100 (if `links.next` is null the count is exact; if there is a next page report ">=100". A 100-code page is large when the pool is big: for big pools use page_size 5 just to see whether a next page exists). Which coupons are in which templates: `get_email_template` and search for `coupon_code` tags (templates change rarely; reuse the last known mapping unless flows changed). Fill `NA.cp[]` {n, where, asg, left, used, matched, low?}.

## 5. Warm leads (D.leads)
BigQuery on `order_items`, US, order_type='Order', since 36 months before today, email present, is_original_version null/1, order-level net_revenue_usd > 0, grouped per email: orders, LTV, AOV, largest order, first/last order, active months, est. margin. Keep customers with largest order >= 2000 OR (orders >= 3 AND AOV >= 600); top 400 by LTV. Row shape: {email, fn, ln, phone, city, st, orders, ltv, aov, max, first, last}. This contains customer PII: it goes only into the HTML delivered to the user, NEVER into git. Save the K-SQL/BQ output to a file (large results are saved automatically) and parse with python `-I`.

## 6. Build and deliver
1. Write `data/latest.json` = {"D": {flows, msgs, enr, top, issues, coupons (legacy, may be []), leads, meta}, "cohort": [...], "NA": {...}}; `D.flows` entries {id,name,short,c:"--s1".."--s4"}; `D.meta` = {asof:"YYYY-MM-DD", range:"Oct 31 – Nov 6", range_long, utc_range:"Oct 31 – Nov 7 UTC", asof_long:"Nov 6 2026"} (use today's date in America/Phoenix).
2. `python3 scripts/build_dashboard.py data/latest.json out/B2B_Dashboard.html`.
3. Narrative strings inside the template that mention specific dates/facts must be reviewed each run (grep the template for them and edit the template text in the repo if stale): "Everything was sent on Nov 4", "All four flows went live Nov 2–4", "17 / 25 / 10 / 12 / 9 per day", "Nov 4 sends", "Nov 4 is partial", "B2B email campaigns (Nov 4 sends)", campaign summary note under that table, the coupon bullet list ("4,131 campaign sends", "TRADE15/20 ... 2 unassigned"), and the flow-health issues (data-driven). Update numbers in those strings from the fresh data.
4. Sanity-check before sending: load the HTML in headless Chromium (Playwright), click each of the 4 tabs, confirm no console errors, screenshot the B2B sales tab and look at it. Compare US net sales for Oct 31–Nov 4 with the previous snapshot in `data/history/` (shouldn't change by more than a few percent; late cancellations can move it).
5. SendUserFile the HTML (`display: "render"`, status proactive) with a 3-line caption: key numbers (B2B cohort net sales and % of US, flow recipients/CVR) and any new HIGH issues.
6. Commit `data/history/YYYY-MM-DD.json` (D without `leads`, plus cohort and NA) and any template text edits; push. Never commit the HTML or any customer data.

## Known limits (state them in the delivered caption if relevant)
- Cohort list/segment membership is CURRENT membership; flow-email and form signals are ever-since-Nov-2023.
- Klaviyo day boundaries from K-SQL are UTC; the last day is partial.
- B2B attribution table uses Klaviyo order value, not BigQuery net sales.
- Margin is estimated PRODUCT margin only.

## Run log
- 2026-11-04 test run: worked end to end. Fixes found: STRING_AGG unsupported; K-SQL mistyped a flow ID; CRTRA10 excluded from the coupon signal; stale narrative strings updated in the template.
