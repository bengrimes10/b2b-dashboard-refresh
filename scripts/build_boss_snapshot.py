#!/usr/bin/env python3
"""Private, read-only B2B sales snapshot for two requested 2026 periods."""
from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from html import escape
import json
import os
from pathlib import Path
import re

from google.cloud import bigquery

import refresh_dashboard as base


PERIODS = [
    ("October 2026", date(2026, 10, 1), date(2026, 10, 31)),
    ("Oct 31 – Nov 8", date(2026, 10, 31), date(2026, 11, 8)),
]
OUT = base.ROOT / "out"

ORDER_SQL = f"""
WITH c AS (
  SELECT JSON_VALUE(x, '$.order_name') order_name, JSON_VALUE(x, '$.fl') fl
  FROM UNNEST(JSON_QUERY_ARRAY(@flags_json)) x
), shop AS (
  SELECT order_name, MIN(DATE(order_created_at)) d,
    SUM(line_item_quantity) units
  FROM `{base.PROJECT}.mart_ecom_tracker.order_items`
  WHERE country='US' AND order_type='Order' AND DataSource='Shopify'
    AND (is_original_version IS NULL OR is_original_version=1)
    AND DATE(order_created_at) BETWEEN @start AND @end
  GROUP BY 1
), nbs AS (
  SELECT order_name, ROUND(SUM(net_sales_usd),2) net
  FROM `{base.PROJECT}.mart_ecom_tracker.net_sales_bookings`
  WHERE country='US' AND date_ref BETWEEN @start AND @end
  GROUP BY 1
), big AS (
  SELECT n.order_name FROM nbs n JOIN shop USING(order_name) WHERE n.net>=3000
), ca AS (
  SELECT COALESCE(c.order_name,b.order_name) order_name, IFNULL(c.fl,'') fl,
    b.order_name IS NOT NULL is_big
  FROM c FULL JOIN big b USING(order_name)
)
SELECT ca.order_name, CAST(shop.d AS STRING) d, nbs.net, shop.units,
  ca.fl, ca.is_big,
  (ca.is_big AND NOT REGEXP_CONTAINS(ca.fl,r'[lsfmc]')) big_only
FROM ca JOIN shop USING(order_name) JOIN nbs USING(order_name)
WHERE (REGEXP_CONTAINS(ca.fl,r'[lsfmc]') OR ca.is_big)
ORDER BY nbs.net DESC, ca.order_name
"""

TOTAL_SQL = f"""
SELECT ROUND(SUM(net_sales_usd),2) net
FROM `{base.PROJECT}.mart_ecom_tracker.net_sales_bookings`
WHERE country='US' AND date_ref BETWEEN @start AND @end
"""


def bq(client, sql, start, end, flags=None):
    params = [bigquery.ScalarQueryParameter("start", "DATE", start),
              bigquery.ScalarQueryParameter("end", "DATE", end)]
    if flags is not None:
        params.append(bigquery.ScalarQueryParameter("flags_json", "STRING", json.dumps(flags)))
    cfg = bigquery.QueryJobConfig(query_parameters=params)
    return [dict(row.items()) for row in client.query(sql, job_config=cfg, location="EU").result()]


def money(value):
    return round(float(value or 0), 2)


def dollars(value):
    return f"${money(value):,.2f}"


def coupon_family(code):
    code = str(code or "").strip().upper()
    for prefix in ("TRADE_TRD", "TRADE_TRA", "TRD500_EMAIL", "TRADE10", "TRADE15", "TRADE20", "SBB2B10"):
        if code.startswith(prefix):
            return prefix
    if code.startswith("TRD"):
        return "TRD* code family (coupon key unverified)"
    if code.startswith("TRADE") and code != "TRADE DISCOUNT":
        return "Other TRADE* code family"
    return code[:45]


def report_rows(api, kind, start, end):
    attrs = {"statistics": base.STATISTICS,
             "timeframe": {"start": datetime.combine(start, datetime.min.time(), base.TZ).isoformat(),
                           "end": datetime.combine(end + timedelta(days=1), datetime.min.time(), base.TZ).isoformat()},
             "conversion_metric_id": "WvaquY",
             "group_by": (["flow_id", "flow_message_id", "flow_message_name"] if kind == "flow"
                          else ["campaign_id", "campaign_message_id", "campaign_message_name"])}
    if kind == "flow":
        attrs["filter"] = 'contains-any(flow_id,' + json.dumps(base.FLOW_IDS, separators=(",", ":")) + ')'
    out = []
    for row in api.report(kind, attrs):
        g, s = row.get("groupings") or {}, row.get("statistics") or {}
        name = g.get("flow_message_name" if kind == "flow" else "campaign_message_name") or "Unnamed email"
        if kind == "campaign" and not re.search(r"trade|commercial|b2b", name, re.I):
            continue
        if kind == "flow" and re.search(r"\bSMS\b|_SMS", name, re.I):
            continue
        fid = g.get("flow_id", "")
        out.append({"name": name, "flow": base.FLOW_NAMES.get(fid, fid),
                    "recipients": int(s.get("recipients") or 0),
                    "conversions": money(s.get("conversion_uniques")),
                    "klaviyo_value": money(s.get("conversion_value"))})
    return sorted(out, key=lambda x: x["klaviyo_value"], reverse=True)


def resolve_names(api):
    """Use current account names instead of labels copied from an older handoff."""
    for item in api.pages("flows", {"page[size]": 50, "fields[flow]": "name,status"}):
        if item["id"] in base.FLOW_IDS:
            base.FLOW_NAMES[item["id"]] = item["attributes"]["name"]

    tasks = [("lists", item_id) for item_id in base.CORE_LISTS]
    tasks += [("segments", item_id) for item_id in base.CORE_SEGMENTS]

    def fetch(task):
        kind, item_id = task
        worker = base.Klaviyo(api.key)
        return kind, item_id, worker.request("GET", f"{kind}/{item_id}")["data"]["attributes"]["name"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        for kind, item_id, name in pool.map(fetch, tasks):
            (base.LIST_NAMES if kind == "lists" else base.SEGMENT_NAMES)[item_id] = name
    print("Verified current names for flows, lists, and segments", flush=True)


def aggregate(orders, details, code_map):
    groups = {name: defaultdict(lambda: {"orders": set(), "sales": 0.0})
              for name in ("lists", "segments", "flows", "forms", "coupons")}
    for order in orders:
        name = order["order_name"]
        det = details.get(name) or {}
        values = {"lists": det.get("lists") or [], "segments": det.get("segs") or [],
                  "flows": det.get("flows") or [], "forms": det.get("forms") or [],
                  "coupons": [coupon_family(c) for c in code_map.get(name, [])
                              if re.search(r"trade|trd|b2b", str(c), re.I)
                              and str(c).upper() != "CRTRA10"]}
        for kind, names in values.items():
            for item in set(names):
                group = groups[kind][item]
                group["orders"].add(name)
                group["sales"] += money(order["net"])
    return {kind: sorted([{"name": key, "orders": len(val["orders"]),
                           "sales": round(val["sales"], 2)} for key, val in groups[kind].items()],
                         key=lambda x: x["sales"], reverse=True)
            for kind in groups}


def td(value, numeric=False):
    return f'<td class="num">{escape(str(value))}</td>' if numeric else f'<td>{escape(str(value))}</td>'


def table(headers, rows, empty="No matching records in this period."):
    if not rows:
        return f'<p class="empty">{escape(empty)}</p>'
    head = ''.join(f'<th>{escape(h)}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(td(value, i > 0) for i, value in enumerate(row)) + '</tr>' for row in rows)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def make_html(periods, generated_at):
    sections = []
    for p in periods:
        orders = p["orders"]
        net = sum(money(o["net"]) for o in orders)
        big_only = [o for o in orders if o["big_only"]]
        big_all = [o for o in orders if o["is_big"]]
        share = 100 * net / p["us_net"] if p["us_net"] else 0
        cards = [
            ("B2B net sales", dollars(net)), ("B2B orders", f'{len(orders):,}'),
            ("Share of US net", f'{share:.1f}%'),
            ("$3k+ orders", f'{len(big_all):,} · {dollars(sum(money(o["net"]) for o in big_all))}'),
            ("$3k+ without core signal", f'{len(big_only):,} · {dollars(sum(money(o["net"]) for o in big_only))}')]
        card_html = ''.join(f'<div class="card"><span>{escape(label)}</span><strong>{escape(value)}</strong></div>'
                            for label, value in cards)
        breakdowns = []
        for kind, title in (("lists", "Lists"), ("segments", "Segments"),
                            ("flows", "Flows received"), ("forms", "Forms submitted"),
                            ("coupons", "Coupons used on order")):
            rows = [(x["name"], f'{x["orders"]:,}', dollars(x["sales"])) for x in p["groups"][kind]]
            breakdowns.append(f'<section><h3>{title}</h3>' + table(["Signal", "Orders", "Associated net sales"], rows) + '</section>')
        email_sections = []
        for kind, title in (("flow_emails", "Flow emails"), ("campaign_emails", "B2B campaign emails")):
            rows = [(f'{e["flow"]} · {e["name"]}' if kind == "flow_emails" else e["name"],
                     f'{e["recipients"]:,}', f'{e["conversions"]:,.1f}', dollars(e["klaviyo_value"]))
                    for e in p[kind]]
            email_sections.append(f'<section><h3>{title}</h3>' + table(
                ["Email", "Recipients", "Klaviyo conversions", "Klaviyo attributed value"], rows,
                "No matching B2B email report rows for this period.") + '</section>')
        top = []
        for rank, order in enumerate(orders[:25], 1):
            det = p["details"].get(order["order_name"]) or {}
            signals = []
            for label, key in (("List", "lists"), ("Segment", "segs"), ("Flow", "flows"), ("Form", "forms")):
                if det.get(key): signals.append(label + ': ' + ', '.join(det[key]))
            if det.get("coupon"): signals.append('Coupon: ' + coupon_family(det["coupon"]))
            if order["big_only"]: signals.append('$3k+ with no core B2B signal')
            elif order["is_big"]: signals.append('$3k+')
            top.append((f'{rank:02d}', order["order_name"], order["d"], dollars(order["net"]),
                        ' • '.join(signals) or 'B2B signal'))
        sections.append(f'''<article id="{p['id']}"><div class="eyebrow">{escape(p['start'])} through {escape(p['end'])}</div>
          <h2>{escape(p['label'])}</h2><div class="cards">{card_html}</div>
          <p class="note">Net sales are from BigQuery. {sum(money(o['net']) == 0 for o in orders)} zero-net orders are included in the order count but contribute no sales. The rows below show signals linked to B2B orders; one order may appear in several rows. Coupon rows combine one time codes by recognizable family; TRD* cannot be assigned to a verified coupon key.</p>
          <div class="grid">{''.join(breakdowns)}</div>
          <h3>Email performance</h3><p class="note">Klaviyo's attributed email value uses its own attribution and order-value rules. It is shown separately and must not be added to BigQuery B2B net sales.</p>
          <div class="grid">{''.join(email_sections)}</div>
          <section><h3>Top 25 B2B orders by net sales</h3>{table(['#', 'Order', 'Date', 'Net sales', 'Qualifying signals'], top)}</section></article>''')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <title>B2B sales snapshot · October and Oct 31–Nov 8 2026</title>
      <style>body{{margin:0;background:#f5f7f9;color:#172b3a;font:15px/1.5 Arial,sans-serif}}header{{background:#10384b;color:white;padding:32px max(28px,calc((100vw - 1200px)/2))}}header h1{{margin:0;font-size:32px}}header p{{max-width:900px;color:#d6e6ec}}main{{max-width:1200px;margin:auto;padding:28px}}nav a{{display:inline-block;margin:0 10px 8px 0;padding:10px 16px;background:#fff;border:1px solid #d5e0e6;border-radius:8px;color:#10384b;text-decoration:none;font-weight:bold}}article{{margin:24px 0 52px}}h2{{font-size:27px;margin:0 0 18px}}h3{{font-size:19px;margin:0 0 12px}}section{{background:#fff;border:1px solid #dbe4e8;border-radius:12px;padding:20px;margin:18px 0;min-width:0}}.eyebrow{{font-weight:bold;text-transform:uppercase;letter-spacing:.08em;color:#507084}}.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(185px,1fr));gap:12px}}.card{{background:#fff;border:1px solid #dbe4e8;border-radius:12px;padding:16px}}.card span{{display:block;color:#567080;font-size:13px}}.card strong{{display:block;font-size:24px;margin-top:4px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:0 18px}}.table-wrap{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:13px}}th{{background:#ecf3f6;text-align:left;color:#344d5c}}th,td{{padding:9px 10px;border-bottom:1px solid #e6edf0;vertical-align:top}}td.num{{text-align:right;white-space:nowrap}}.note{{color:#4f6573;max-width:1000px}}.empty{{color:#6c7f89}}footer{{margin-top:40px;padding:24px;border-top:1px solid #dbe4e8;color:#506876}}@media print{{body{{background:white}}nav{{display:none}}article{{break-before:page}}section,.card{{break-inside:avoid}}}}</style></head>
      <body><header><h1>B2B sales snapshot</h1><p>Two requested views: October 1–31, 2026 and October 31–November 8, 2026. October 31 appears in both views by design. Generated {escape(generated_at)} America/Phoenix.</p></header>
      <main><nav><a href="#october">October 2026</a><a href="#oct31_nov8">Oct 31–Nov 8</a></nav>{''.join(sections)}
      <footer><strong>Definition and limits.</strong> US Shopify orders count as core B2B when the buyer is currently in a designated core list or segment, has ever received a designated Trade/Commercial/B2B flow email or submitted a designated form, used a trade/trd/b2b discount code (excluding CRTRA10), or the order's BigQuery net sales are at least $3,000. Extended-only memberships are excluded unless another core condition applies. Current list/segment membership and historical email/form signals may postdate the order; association is not proof of causation. BigQuery net sales include later order adjustments recorded within each reporting window. Samples are excluded. No customer names, contact information, or addresses appear in this report.</footer></main></body></html>'''


def main():
    if not os.getenv("KLAVIYO_PRIVATE_API_KEY"):
        import keyring
        key = keyring.get_password("selectblinds-b2b-dashboard", "klaviyo-readonly")
    else:
        key = os.environ["KLAVIYO_PRIVATE_API_KEY"]
    if not key:
        raise RuntimeError("Klaviyo key missing from Windows Credential Manager")
    api = base.Klaviyo(key)
    client = bigquery.Client(project=base.BILLING_PROJECT)
    resolve_names(api)
    audiences = base.all_memberships(api)
    cache = base.signal_cache(api, datetime.now(timezone.utc))
    start_dt = datetime(2026, 10, 1, tzinfo=base.TZ).astimezone(timezone.utc)
    end_dt = datetime(2026, 11, 9, tzinfo=base.TZ).astimezone(timezone.utc)
    flags, codes, eligible, details = base.order_flags(
        api, start_dt, end_dt, audiences["core_lists"], audiences["core_segments"],
        audiences["ext_lists"], audiences["ext_segments"], cache)
    print(f"Qualified order events checked: {eligible}; flagged order names: {len(flags)}", flush=True)
    periods = []
    for label, start, end in PERIODS:
        orders = bq(client, ORDER_SQL, start, end, flags)
        us_net = money(bq(client, TOTAL_SQL, start, end)[0]["net"])
        flow_emails = report_rows(api, "flow", start, end)
        campaign_emails = report_rows(api, "campaign", start, end)
        period = {"id": "october" if start.day == 1 else "oct31_nov8",
                  "label": label, "start": start.isoformat(), "end": end.isoformat(),
                  "us_net": us_net, "orders": orders, "groups": aggregate(orders, details, codes),
                  "details": {x["order_name"]: details.get(x["order_name"], {}) for x in orders[:25]},
                  "flow_emails": flow_emails, "campaign_emails": campaign_emails}
        periods.append(period)
        print(f"{label}: {len(orders)} B2B orders; {dollars(sum(money(o['net']) for o in orders))} net; "
              f"{sum(bool(o['big_only']) for o in orders)} large-only", flush=True)
    OUT.mkdir(exist_ok=True)
    generated_at = datetime.now(base.TZ).strftime("%B %d, %Y %I:%M %p")
    path = OUT / "B2B_Sales_Snapshot_October_and_Oct31_Nov8_2026.html"
    path.write_text(make_html(periods, generated_at), encoding="utf-8")
    audit = OUT / "B2B_Sales_Snapshot_October_and_Oct31_Nov8_2026.json"
    audit.write_text(json.dumps(periods, default=str, ensure_ascii=False), encoding="utf-8")
    print("Private snapshot:", path, flush=True)


if __name__ == "__main__":
    main()
