# Handoff: B2B dashboard and tracking (for Codex)

Written 2026-11-09 by Claude. Owner: benjamin.grimes@selectblinds.com (SelectBlinds). This file is both the briefing and the prompt. Section 0 is the prompt to paste into Codex; the rest is the reference it points to.

---

## 0. Prompt to paste into Codex

You are taking over a daily B2B reporting job for SelectBlinds (US ecommerce, Shopify, Klaviyo, BigQuery). Until now another AI agent ran it inside its own session using built-in Klaviyo and BigQuery connectors. You will not have those connectors, so you must rebuild the data pulls yourself with APIs and scripts, and keep the dashboard output identical in meaning.

Your job, every day at 05:13 America/Phoenix:
1. Pull fresh data from Klaviyo and BigQuery (read-only) for 2026-10-31 through today.
2. Rebuild one self-contained HTML dashboard from `template/dashboard_template.html` with `scripts/build_dashboard.py`.
3. Verify it renders (4 tabs, no console errors) with headless Chromium.
4. Deliver the HTML to the owner (see section 3 for the delivery channel, which still needs to be chosen).
5. Commit and push ONLY the metrics snapshot `data/history/YYYY-MM-DD.json` and any template text edits. Never commit customer emails, names, phone numbers, the generated HTML, or `data/latest.json`.

Start by reading, in this order: this file, `README.md`, `RUNBOOK.md` (the previous agent's procedure; the business rules in it are correct, but its tool calls are specific to that agent), then `template/dashboard_template.html` and `scripts/build_dashboard.py`.

Before building anything, do these checks and report back to the owner:
- Confirm you have the credentials in section 3. List what is missing.
- Run your pipeline for 2026-10-31 through 2026-11-08 and reproduce the checkpoint numbers in section 9. If any number is off by more than 1 percent, stop and explain the difference before going live.
- Do not change the business definitions in section 4 without the owner's written approval.

Rules: read-only on Klaviyo and BigQuery; never send, schedule, edit or delete anything in Klaviyo or Shopify; no PII in git; do not invent numbers (if a pull fails, say what is stale and deliver what you can); ask the owner when a definition is ambiguous.

---

## 1. What this is

An interactive dashboard (one HTML file, no external libraries, SVG charts, dark/light toggle) with four tabs:
- **Flows**: performance of the live B2B email flows, flow-health issues, funnel, coupons.
- **New enrollees**: new people entering each live flow per day.
- **Warm leads**: past customers who bought big or often (top 400 by LTV). Contains PII.
- **B2B sales**: B2B buyers' net sales, orders, margin, share of US sales, by qualifying signal, by day; drill-downs by flow/campaign/coupon; and the ranked "Largest single B2B orders" table (PII).

The point: SelectBlinds re-engaged B2B/trade leads with Klaviyo flows and campaigns starting 2026-11-02/04. The owner wants to see what that produced, where B2B sales come from, and where the setup is broken.

## 2. Repository (github.com/bengrimes10/b2b-dashboard-refresh, private, branch main)

- `template/dashboard_template.html`: the dashboard; placeholders `__D__`, `__COHORT__`, `__NA__`; contains narrative strings that mention dates and numbers and must be reviewed daily (section 8).
- `scripts/build_dashboard.py`: `python3 scripts/build_dashboard.py data/latest.json out/B2B_Dashboard.html`.
- `data/latest.json` (git-ignored, contains PII): input to the build. Shape in section 6.
- `data/history/YYYY-MM-DD.json`: daily metrics snapshots (leads and `D.big` emptied). Definitions changed on 2026-11-09 (the large-order rule), so compare snapshots from 2026-11-09 onward only.
- `data/sample_data.json`: example data.
- `RUNBOOK.md`: previous agent's procedure and run log (read the run log; it lists every gotcha).
- `.gitignore`: ignores `data/latest.json` and `out/`.

## 3. Access and delivery (the owner must supply these)

- **Klaviyo**: a private API key with read scopes (profiles, lists, segments, flows, campaigns, events, metrics, coupons, forms, reporting). Account id UzEnEn, US, USD, timezone America/Phoenix (US/Arizona).
- **BigQuery**: read access to project `hd-global-analytics-platform` (datasets `mart_ecom_tracker`; EU location). Service account with BigQuery Data Viewer and Job User.
- **GitHub**: push access to the repo above.
- **Scheduling**: a scheduler that runs the job at 05:13 America/Phoenix (cron `13 5 * * *` with `TZ=America/Phoenix`).
- **Delivery**: the previous agent sent the HTML into the owner's chat. Codex needs another channel: ask the owner to choose (email attachment, Slack file, private shared drive). The file contains customer PII, so it must go only to the owner.
- **Shopify**: direct Shopify access is NOT allowed. Everything about orders comes from Klaviyo events and BigQuery.

Important: the previous agent used Klaviyo's "ask analytics question" tool (K-SQL), which is an LLM-written SQL feature that is not part of the public API. Do not depend on it. Replace it with the approach in section 5 (public API exports, or Klaviyo data synced into BigQuery if the owner has that). The queries it ran are described so you can reproduce their results exactly.

## 4. Business definitions (do not change without approval)

Date range: 2026-10-31 through today. Sales charts end at the last full day BigQuery has (BigQuery trails by about a day; today is partial and can even be negative).

**US totals**: `net_sales_bookings` (net_sales_usd, gross_sales_usd) for country='US'; orders from `order_items` (order_type='Order', i.e. samples excluded; `is_original_version` IS NULL or 1; distinct order_name for Shopify, else order_id); `order_items.net_revenue_usd` and `estimated_cost_usd` for margin. Margin = (net revenue - estimated product cost) / net revenue. It is PRODUCT margin only; estimated cost covers only about a third of lines; say so on the page.

**B2B cohort** = US Shopify orders (samples excluded) that meet ANY of:
- (l) the buyer is currently a member of a core list: RArqFN, SuJXq5, XYuXP5, S3uABq, TM29XF, UeKxx2, UwUddt
- (s) the buyer is currently in a core segment: UtP84e, U2Z8YK, VExknn, Ycbxb4, R77LxD, Yb9xBp, UMxDa4
- (f) the buyer ever received an email (since Nov 2023) from any of these 15 flows: XA5MGj SZMWtA SVNQ6Y RjvnXL QTXBNv R77Xza T8tDXU TgGSgX ThRrHh XV5Jxt XcCt2k Y89wS2 YiK4az SWgfrf YnCsYM
- (m) the buyer ever submitted one of these forms (since Nov 2023): Uxsw3c VpZjF6 Rjbv4E Sx2BJq U5HrsT Yewkfv VBYJhK VDnyVy
- (c) the order used a discount code containing trade, trd or b2b (case-insensitive), checking each code individually, and NOT counting CRTRA10 (excluded on purpose until the owner confirms what it is; it appears on ~95 orders / $44.8K)
- (b) the order's total net sales are >= $3,000 (raised from $2,000 on 2026-11-09 by the owner's decision). This counts on its own; no other signal needed. Net sales per order = SUM(net_sales_usd) in `net_sales_bookings` for that order_name.
Orders with net sales <= 0 are dropped from the ranked list.

**Extended tier** (toggle, kept separate): buyers currently in lists Rh4564, VGrNiu or segments SmZ4HF, XsSRLS, who do not meet any core signal. Not B2B on its own.

**Large-order bookkeeping**: `nb/sb` = all $3,000+ orders (count, net sales); `nbo/sbo` = $3,000+ orders with no other core signal ("big only"). They are inside `o/net`.

Names (do not rename): lists RArqFN Trade - Business Interest Only; SuJXq5 B2B Masterlist; XYuXP5 Commercial - Website RAQ; S3uABq B2B Masterlist - Inactive; TM29XF Commercial/B2B Internal Additions; UeKxx2 Commercial/B2B Inquiries from Website; UwUddt Old Commercial Inquiries. Segments UtP84e Trade TEC Results; U2Z8YK Official Master List (All); VExknn Official Master List (Can Receive Mktg); Ycbxb4 Trade Membership Level; R77LxD Trade Silver; Yb9xBp Trade Coupon Used Reissue; UMxDa4 Old Commercial Inquiries. Flows: live XA5MGj Ex-Trade Members - Welcome Series, SZMWtA Ex-Trade Members (TEC) - Welcome Series, SVNQ6Y B2B - Welcome Series, RjvnXL B2B Masterlist - Re-Entry Flow; drafts QTXBNv, R77Xza, XV5Jxt (Trade Membership Assignment - First Upload from Listrak, variants), T8tDXU Trade - Initial Application Thanks + Membership Assignment, TgGSgX B2B-Like Customers - Invite to Trade/Commercial, ThRrHh Trade - New Tier Level - Silver, XcCt2k Trade - Membership Level Communication, Y89wS2 Trade - TEC Accepted/Not Accepted, YiK4az Trade - Coupon Used - Reissue New, SWgfrf Old Commercial Inquiries, YnCsYM Commercial Pricing Inquiry. Forms: Uxsw3c Shopify - Trade Application Form, VpZjF6 Shopify - Commercial Inquiry Form, VBYJhK B2B Audience - News Pop In, Rjbv4E Invited Trade Pop Up, Sx2BJq Trade Reminder Flyout, U5HrsT Trade Application Form, Yewkfv Commercial Inquiry Form, VDnyVy B2B - President's Day Extra 5% Off $750 Email Sign Up.

Metric ids (Klaviyo): Placed Order (Shopify) WvaquY; Placed Order (API) XiwTkV; Received Email YiWaij; Coupon Assigned Ypku5R; Coupon Used XasyRj; Form completed Ts4RUD; Form submitted by profile (API) XDVFye. Conversion metric for all reports: WvaquY.

Live flows in scope: re-check daily with a name filter (Trade, Commercial, B2B) and status live. Currently XA5MGj, SZMWtA, SVNQ6Y, RjvnXL.

## 5. Daily data pulls and how to do them without the old tools

### 5.1 Flow performance (Flows page, `D.msgs`)
Klaviyo Reporting API `flow-values-reports`: statistics recipients, delivered, opens_unique, clicks_unique, conversion_uniques, unsubscribe_uniques, bounced, spam_complaints; value_statistics conversion_value; group by flow_id, flow_message_id, flow_message_name; timeframe 2026-10-31T00:00-07:00 to now; conversion metric WvaquY; filter flow ids = the live four. Output rows: `{f, m, rec, del, opens, clicks, conv, rev, unsub, bounced, spam}`. A live flow with no row gets a zero row. Message names are shortened to "Welcome #1: Never Purchased" etc.

### 5.2 New enrollees (`D.enr`)
Metric aggregates on Received Email (YiWaij), measurement unique, by $flow, interval day, timezone US/Arizona, filtered to the live flows. One row per day from 2026-10-31: `{d:'YYYY-MM-DD', <flowId>: n}` with zeros filled.

### 5.3 Campaigns (`NA.camp`, `NA.sends`)
`campaign-values-reports` with name contains Commercial, B2B or Trade, same timeframe and conversion metric. Rows `{n, aud, sent, rec, dl, opens, clicks, conv, rev, unsub}`; `NA.sends = {flows: total live-flow recipients, camps: total recipients of those campaigns}`.

### 5.4 Coupons (`NA.cp`, `D.issues`)
- Assigned counts since 2026-10-31 per coupon key (TRADE10, TRADE15, TRADE20, Trade_TRD, Trade_TRA, TRD500_Email, keys starting B2B): events of the Coupon Assigned metric (Ypku5R) grouped by CouponKey.
- Codes left: Coupon Codes API filtered coupon.id and status UNASSIGNED; page_size 5; if `links.next` is null the count is exact, otherwise report ">=100". Currently TRADE15 and TRADE20 have 0 unassigned; TRADE10 not verified recently (the call timed out; last seen >=100).
- Klaviyo's own "Used" status stays 0 (not syncing). Redemption is measured by matching Shopify order discount codes: a Klaviyo unique code looks like `TRADE10XXXXXXXX` (code string on the order, e.g. TRADE10792Z652X). Redeemed so far: US131261, US132518, US134348 (TRADE10), US133638 (TRADE15).
- Skipped sends: Klaviyo logs "Skipped: No More Shopify Coupons Remain" (recipient_status SHOPIFY_COUPON_UNAVAILABLE) in its skipped-send events. This is a key health check: 6,134 campaign emails were skipped on 2026-11-06. Check daily. Flow skips are not logged there.

### 5.5 Cohort flags per order (the hard part)
Goal: for every US Shopify order from 2026-10-31 with value > 0, compute flags l, s, f, m, c (x for extended), then join to BigQuery by Shopify order name (format `US` + 6 digits, e.g. US130346).
Public-API way:
1. Export members of each core list and segment (Profiles API / list and segment relationships, or bulk export) -> set of profile ids.
2. Pull Placed Order (Shopify) events from 2026-10-31 (Events API, metric WvaquY): profile id, order name (event property `name`/`OrderId` per the account's payload), order value, discount codes.
3. Pull Received Email events (metric YiWaij) for the 15 flows over the last 1,096 days -> set of profile ids with a flow email. Pull Form-submitted events for the 8 forms -> profile ids.
4. Flag each order. Use order value > 0 only in the Klaviyo step (samples are $0). Drop orders BigQuery excludes (samples, non-US, non-Shopify).
Encode each flagged order as `<order_name><letters>` where letters l,s,f,m,c,x, e.g. `US129755lsfmcx`. (If you have the Klaviyo data in BigQuery, do this as a SQL join instead; then you do not need the encoded string.)
Overlap windows: always re-query from about 6 hours before the previous run, because list/segment membership is current membership and the previous run ends at its own run time. Dedupe by order_name.

### 5.6 Attribution table (`NA.attr`)
Klaviyo attribution of cohort orders (list/segment members only, value > 0) to flows and campaigns, weighted by attribution weight: rows `{n, id, o, v, b2b}` for flows and campaigns plus `none` (total minus attributed) and `total`. b2b=true for flows named Trade/Commercial/B2B and campaigns named [Commercial]/B2B/Trade. Campaign ids are truncated in the data (first 8 chars + "..."). The dashboard says the table covers list and segment members only; keep that note true.

### 5.7 US totals and cohort in BigQuery
Per-day US totals (`D.top`): net, gross, orders, oi (net revenue), cost:
```sql
WITH nb AS (SELECT date_ref d, SUM(net_sales_usd) net, SUM(gross_sales_usd) gross
  FROM `hd-global-analytics-platform.mart_ecom_tracker.net_sales_bookings`
  WHERE country='US' AND date_ref BETWEEN '2026-10-31' AND CURRENT_DATE() GROUP BY 1),
oi AS (SELECT DATE(order_created_at) d,
  COUNT(DISTINCT IF(DataSource='Shopify', order_name, CAST(order_id AS STRING))) orders,
  SUM(net_revenue_usd) oi_net, SUM(estimated_cost_usd) est_cost
  FROM `hd-global-analytics-platform.mart_ecom_tracker.order_items`
  WHERE country='US' AND order_type='Order' AND (is_original_version IS NULL OR is_original_version=1)
    AND order_created_at BETWEEN '2026-10-31' AND CURRENT_DATE() GROUP BY 1)
SELECT FORMAT_DATE('%Y-%m-%d', nb.d) d, ROUND(nb.net,2) net, ROUND(nb.gross,2) gross, oi.orders,
  ROUND(oi.oi_net,2) oi_net, ROUND(oi.est_cost,2) est_cost
FROM nb LEFT JOIN oi ON oi.d=nb.d ORDER BY 1
```
Cohort per day (`cohort[]`): feed the encoded orders in and add the large-order rule. `<ENCODED>` is the space-separated list from 5.5:
```sql
WITH raw AS (SELECT x FROM UNNEST(SPLIT('<ENCODED>', ' ')) x),
c AS (SELECT SUBSTR(x,1,8) order_name, SUBSTR(x,9) fl FROM raw WHERE x != ''),
shop AS (SELECT order_name, MIN(order_created_at) d, SUM(net_revenue_usd) oi_net, SUM(estimated_cost_usd) est_cost
  FROM `hd-global-analytics-platform.mart_ecom_tracker.order_items`
  WHERE country='US' AND order_type='Order' AND DataSource='Shopify'
    AND order_created_at BETWEEN '2026-10-31' AND CURRENT_DATE() GROUP BY 1),
nbs AS (SELECT order_name, SUM(net_sales_usd) net_sales
  FROM `hd-global-analytics-platform.mart_ecom_tracker.net_sales_bookings`
  WHERE country='US' AND date_ref BETWEEN '2026-10-31' AND CURRENT_DATE() GROUP BY 1),
big AS (SELECT order_name FROM nbs WHERE net_sales >= 3000 AND order_name IN (SELECT order_name FROM shop)),
ca AS (SELECT COALESCE(c.order_name, b.order_name) order_name, IFNULL(c.fl,'') fl, b.order_name IS NOT NULL is_big
  FROM c FULL JOIN big b ON c.order_name=b.order_name),
c2 AS (SELECT order_name, (REGEXP_CONTAINS(fl,r'[lsfmc]') OR is_big) core,
  REGEXP_CONTAINS(fl,r'l') f_list, REGEXP_CONTAINS(fl,r's') f_seg, REGEXP_CONTAINS(fl,r'f') f_flow,
  REGEXP_CONTAINS(fl,r'm') f_form, REGEXP_CONTAINS(fl,r'c') f_coupon, is_big f_big,
  (is_big AND NOT REGEXP_CONTAINS(fl,r'[lsfmc]')) big_only FROM ca)
SELECT FORMAT_DATE('%Y-%m-%d', DATE(shop.d)) d, IF(c2.core,'core','ext_only') tier, COUNT(*) orders,
  ROUND(SUM(nbs.net_sales),2) net_sales, ROUND(SUM(shop.est_cost),2) est_cost,
  COUNTIF(f_list) n_list, COUNTIF(f_seg) n_seg, COUNTIF(f_flow) n_flow, COUNTIF(f_form) n_form, COUNTIF(f_coupon) n_coupon,
  ROUND(SUM(IF(f_list,nbs.net_sales,0)),2) s_list, ROUND(SUM(IF(f_seg,nbs.net_sales,0)),2) s_seg,
  ROUND(SUM(IF(f_flow,nbs.net_sales,0)),2) s_flow, ROUND(SUM(IF(f_form,nbs.net_sales,0)),2) s_form,
  ROUND(SUM(IF(f_coupon,nbs.net_sales,0)),2) s_coupon,
  COUNTIF(f_big) n_big, ROUND(SUM(IF(f_big,nbs.net_sales,0)),2) s_big,
  COUNTIF(big_only) n_bigonly, ROUND(SUM(IF(big_only,nbs.net_sales,0)),2) s_bigonly
FROM c2 JOIN shop USING(order_name) LEFT JOIN nbs USING(order_name) GROUP BY 1,2 ORDER BY 1,2
```
Map into `cohort[i]`: `core = {o, net, cost, nl, ns, nf, nm, nc, sl, ss, sf, sm, sc, nb, sb, nbo, sbo}` (from the `core` rows: orders, net_sales, est_cost, n_list, n_seg, n_flow, n_form, n_coupon, s_list, s_seg, s_flow, s_form, s_coupon, n_big, s_big, n_bigonly, s_bigonly) and `ext = {o, net, cost}` from the `ext_only` row; `d` is `MM-DD`. One entry per day, same order and length as `D.top`, zero-filled.

### 5.8 Largest single B2B orders (`D.big`, PII)
Same CTEs as 5.7 (the `shop` CTE also needs `ANY_VALUE(LOWER(TRIM(email))) email, ANY_VALUE(first_name) fn, ANY_VALUE(last_name) ln, ANY_VALUE(shipping_address_city) city, ANY_VALUE(shipping_address_province) st, SUM(line_item_quantity) units, ARRAY_TO_STRING(ARRAY_AGG(DISTINCT product_name IGNORE NULLS LIMIT 2), ' | ') prods`) ending with:
```sql
SELECT ca.order_name, FORMAT_DATE('%Y-%m-%d', DATE(shop.d)) d, ROUND(nbs.net_sales,2) net, shop.units, shop.email,
  shop.fn, shop.ln, shop.city, shop.st, shop.prods, CONCAT(REGEXP_REPLACE(ca.fl,'x',''), IF(ca.is_big,'b','')) sig
FROM ca JOIN shop USING(order_name) JOIN nbs USING(order_name)
WHERE (REGEXP_CONTAINS(ca.fl,r'[lsfmc]') OR ca.is_big) AND nbs.net_sales > 0
ORDER BY nbs.net_sales DESC LIMIT 400
```
Row: `{o, d, net, units, em, fn, ln, city, st, prods, sig, det}` where `det = {lists:[names], segs:[names], flows:[names], forms:[names], coupon:'TRADE10 unique code (CODE)' | 'Trade Discount (manual code)' | ''}` using the readable names in section 4. Lists and segments are current membership; flows and forms mean "ever received/submitted" (a flow email can post-date the order). In `data/history/*.json` set `D.big` to [].

### 5.9 Warm leads (`D.leads`, PII)
BigQuery on `order_items`, US, order_type='Order', last 36 months, email present, `is_original_version` null/1, order-level net_revenue_usd > 0 (group by email and order_name), keep customers with largest order >= 2,000 OR (orders >= 3 AND AOV >= 600), top 400 by LTV. Row: `{email, fn, ln, phone, city, st, orders, ltv, aov, max, first, last}` (use columns `phone_number`, `shipping_address_city`, `shipping_address_province`). The warm-leads page keeps its own $2,000 rule; it is separate from the B2B large-order rule.

## 6. Data file shape (`data/latest.json`)

```
{ "D": { "flows":[{id,name,short,c:"--s1".."--s4"}], "msgs":[...], "enr":[...], "top":[{d,net,gross,orders,oi,cost,b2b:0,b2bo:0}],
         "issues":[{sev:"HIGH|MEDIUM|INFO",flow,issue,evidence,action}], "coupons":[], "leads":[...], "big":[...],
         "meta":{asof:"YYYY-MM-DD", asof_long:"Nov 9 2026", range:"Oct 31 – Nov 8", range_long:"Oct 31 – Nov 8 2026", utc_range:"Oct 31 – Nov 9 UTC"} },
  "cohort": [ {d:"MM-DD", core:{...}, ext:{o,net,cost}} ],
  "NA": { "attr":{none:{o,v},total:{o,v},flows:[],camps:[]}, "camp":[...], "cp":[{n,where,asg,left,used,matched,low?}], "sends":{flows,camps} } }
```
`D.top` and `cohort` must have equal length. `D.meta.range` is the last day with BigQuery data; `utc_range` is the Klaviyo window.

## 7. Build and verify

`python3 scripts/build_dashboard.py data/latest.json out/B2B_Dashboard.html`, then open it in headless Chromium (Playwright), click all four tabs, assert zero console errors, screenshot the B2B sales tab and look at it (and click "Show 25 more" under Largest single B2B orders). Compare the US net sales for the earlier days with the previous snapshot in `data/history/`; late cancellations can move a day by a few percent, more than that needs an explanation.

## 8. Daily review of narrative text and issues (do not skip)

The template has hard-coded sentences that go stale. Each run, grep the template and update from fresh data: the "Most sends went out on ..." line; the flows "went live Nov 2–4 ... spikes" sentence; the account-wide "Added to List" / coupons-assigned sentence (stale; refresh or remove); the coupon bullets (attachment, redemption count and order ids, codes left, B2B-coded order count and dollars); the "Klaviyo returned N qualifying orders; M matched BigQuery" method sentence (N = number of encoded orders, M = sum of cohort `orders` including ext); and the B2B campaigns heading. Rewrite `D.issues` from the facts of the day: drop fixed issues, add new ones, keep severity honest. Current HIGH issues: coupon pools TRADE15/TRADE20 empty and 6,134 campaign sends skipped on Nov 6 (not resent); Re-Entry flow sent ~4,557 emails but only ~9 coupons were assigned since Nov 7 (check one sent email for a blank coupon); segment SAphaG (Trade - Business Interest Only (No Previous Purchases), 2,522 people on Nov 6) never emailed; "first order" offer reaches people who already ordered (the "Never Purchased" branch actually requires a prior order).

## 9. Checkpoint numbers to reproduce (as of the 2026-11-09 run, large-order threshold $3,000)

US net sales by day (BigQuery `net_sales_bookings`): 10-31 220,630.88; 11-01 335,395.12; 11-02 342,628.55; 11-03 236,233.35; 11-04 301,206.37; 11-05 277,981.40; 11-06 242,011.74; 11-07 301,951.57; 11-08 300,228.94 (sum 2,558,267.92; late adjustments can move recent days slightly).
B2B cohort core net sales by day: 10-31 44,406.06 (21 orders); 11-01 51,791.50 (36); 11-02 61,126.93 (31); 11-03 43,686.38 (34); 11-04 47,562.15 (25); 11-05 38,277.16 (21); 11-06 29,197.49 (13); 11-07 37,804.79 (13); 11-08 22,101.30 (17). Total 375,953.76 on 211 orders = 14.7 percent of US net sales. Orders of $3,000+: 55 ($251,188.74); big-only: 38 ($146,184.57).
Largest orders: US133109 $11,786.45; US131048 $11,567.71; US128915 $10,619.01; US127630 $9,963.67; US133040 $8,166.51. Ranked list size: 207 orders with net sales > 0.
Live flows (Oct 31 – Nov 9): Ex-Trade Members 667 recipients, 1 order $223.61; Ex-Trade Members (TEC) 616, 4 orders $6,316.92; B2B Welcome 14, 0; Masterlist Re-Entry 4,557, 2 orders $14,176.66.
Coupons assigned since Oct 31: TRADE10 5,764; TRADE15 4,595; TRADE20 4,595.

## 10. Gotchas the previous agent hit (see the run log in RUNBOOK.md for more)

- BigQuery has no data for today (and the previous day can be partial). Never present today in sales charts.
- `order_created_at` is a DATE; Klaviyo day boundaries are UTC; the flow enrollee counts use US/Arizona.
- Join Klaviyo to BigQuery on the Shopify order name only (`US` + 6 digits). Order names longer than 8 characters need the SUBSTR changed.
- Estimated cost covers about a third of lines: never call margin "actual".
- The coupon-assigned feed shows "Used" = 0 forever; use order discount codes.
- Klaviyo attribution query can time out; the fallback is the list/segment-only version (and the dashboard note says so).
- A single call asking for two days of cohort orders can time out; use windows of about 21 hours with an hour of overlap.
- Do not parse or trust a Klaviyo number you cannot reproduce; verify ids in any generated query (an id was once mistyped).
- PII: `D.leads` and `D.big` contain real customer data. They go only into the HTML sent to the owner. `.gitignore` already covers `data/latest.json` and `out/`; check `git status` before every push.

## 11. Open questions for the owner (do not guess)

1. What is discount code CRTRA10 (95 orders, $44.8K since Oct 31)? Excluded from the B2B coupon signal until answered.
2. Who loads more TRADE10/15/20 codes (Klaviyo codes plus matching Shopify discount codes)? Pools are empty and 6,134 emails were skipped.
3. Should the skipped recipients be re-sent, and should the 2,522 people in segment SAphaG get the coupon email?
4. Delivery channel for the daily HTML (section 3).
5. Is $3,000 the right large-order line? A sample of the 38 big-only orders should be checked by someone who knows which are trade customers.

## 12. Cutover from the previous agent

A scheduled task (id trig_01DvUSePkkQa55s1w5Bfybjy, daily 05:13 America/Phoenix) in the previous agent's session still runs the old pipeline and sends files to the owner. When Codex's pipeline passes the checkpoint comparison, ask the owner to disable or delete that task so there are not two daily runs. That session must stay open until then.
