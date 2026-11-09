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
  SELECT b.order_name, IFNULL(c.fl,'') fl, TRUE is_big
  FROM big b LEFT JOIN c USING(order_name)
)
SELECT ca.order_name, CAST(shop.d AS STRING) d, nbs.net, shop.units,
  ca.fl, ca.is_big,
  (ca.is_big AND NOT REGEXP_CONTAINS(ca.fl,r'[lsfmc]')) big_only
FROM ca JOIN shop USING(order_name) JOIN nbs USING(order_name)
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


def table(headers, rows, empty="No matching records in this period.", numeric_columns=()):
    if not rows:
        return f'<p class="empty">{escape(empty)}</p>'
    head = ''.join(f'<th>{escape(h)}</th>' for h in headers)
    body = ''.join('<tr>' + ''.join(td(value, i in numeric_columns) for i, value in enumerate(row)) + '</tr>' for row in rows)
    return f'<div class="tw"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def daily_chart(orders, start, end):
    """Small SVG in the same blue line-chart style as the imported dashboard."""
    daily = defaultdict(float)
    for order in orders:
        daily[order["d"]] += money(order["net"])
    days = []
    day = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while day <= last:
        days.append(day)
        day += timedelta(days=1)
    values = [daily[d.isoformat()] for d in days]
    ceiling = max(values or [1]) * 1.12 or 1
    left, right, top, bottom = 66, 620, 22, 204
    step = (right - left) / max(len(days) - 1, 1)
    xy = [(left + i * step, bottom - value / ceiling * (bottom - top))
          for i, value in enumerate(values)]
    guides = []
    for fraction in (0, .5, 1):
        y = bottom - fraction * (bottom - top)
        guides.append(f'<line x1="{left}" x2="{right}" y1="{y:.1f}" y2="{y:.1f}" class="chart-grid"/>'
                      f'<text x="{left-10}" y="{y+4:.1f}" text-anchor="end">${ceiling*fraction/1000:,.0f}k</text>')
    label_step = 1 if len(days) <= 10 else 5
    labels = ''.join(f'<text x="{x:.1f}" y="229" text-anchor="middle">{escape(d.strftime("%m-%d"))}</text>'
                     for i, (d, (x, _)) in enumerate(zip(days, xy))
                     if i == 0 or i == len(days)-1 or i % label_step == 0)
    points = ' '.join(f'{x:.1f},{y:.1f}' for x, y in xy)
    dots = ''.join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" fill="var(--s1)"/>'
                   for x, y in xy if len(days) <= 10)
    return f'<svg class="sales-chart" viewBox="0 0 650 245" role="img" aria-label="Daily B2B net sales">{"".join(guides)}<polyline points="{points}" fill="none" stroke="var(--s1)" stroke-width="2.5" stroke-linejoin="round"/>{dots}{labels}</svg>'


def signal_bars(orders):
    rows = []
    for label, predicate in (
        ("Klaviyo list member", lambda o: "l" in o["fl"]),
        ("Klaviyo segment member", lambda o: "s" in o["fl"]),
        ("Received trade/B2B flow email", lambda o: "f" in o["fl"]),
        ("Submitted trade/B2B form", lambda o: "m" in o["fl"]),
        ("Used trade/B2B coupon", lambda o: "c" in o["fl"]),
        ("Order of $3,000+", lambda o: o["is_big"]),
        ("$3,000+ only (no other core signal)", lambda o: o["big_only"]),
    ):
        matches = [o for o in orders if predicate(o)]
        rows.append((label, len(matches), sum(money(o["net"]) for o in matches)))
    maximum = max((value for _, _, value in rows), default=1) or 1
    return ''.join(f'<div class="barrow"><span class="barlabel">{escape(label)}</span>'
                   f'<div class="track"><div class="fill" style="width:{max(0,value)/maximum*100:.1f}%"></div></div>'
                   f'<span class="barvalue">{dollars(value)} <small>{count} orders</small></span></div>'
                   for label, count, value in rows)


STYLE = """
:root{color-scheme:light;--page:#f9f9f7;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);--s1:#2a78d6;--s3:#1baf7a;--ghost:rgba(11,11,11,.05)}
:root[data-theme=dark]{color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s3:#199e70;--ghost:rgba(255,255,255,.07)}
@media(prefers-color-scheme:dark){:root:where(:not([data-theme=light])){color-scheme:dark;--page:#0d0d0d;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);--s1:#3987e5;--s3:#199e70;--ghost:rgba(255,255,255,.07)}}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--page);border-bottom:1px solid var(--border);padding:12px 24px}
h1{font-size:18px;margin:0 0 2px}.sub{color:var(--ink2);font-size:12px}.top{display:flex;justify-content:space-between;gap:16px;align-items:start}
nav{display:flex;gap:4px;margin-top:10px;flex-wrap:wrap}nav button,.btn{font:inherit;border:1px solid var(--border);background:var(--surface);color:var(--ink);padding:6px 12px;border-radius:8px;cursor:pointer}
nav button:hover,.btn:hover{background:var(--ghost)}nav button[aria-selected=true]{background:var(--ink);color:var(--surface);border-color:var(--ink)}
main{padding:20px 24px;max-width:1300px;margin:auto}article[hidden]{display:none!important}.period-title{font-size:18px;margin:4px 0 16px}.eyebrow{color:var(--ink2);font-size:12px;margin-bottom:2px}
.grid{display:grid;gap:16px}.g4{grid-template-columns:repeat(4,minmax(0,1fr))}.g2{grid-template-columns:repeat(2,minmax(0,1fr))}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px;min-width:0}.card h2{font-size:14px;margin:0 0 2px}.card .note,.note{color:var(--ink2);font-size:12px}.card .note{margin:2px 0 10px}
.kpi .v{font-size:28px;font-weight:600;letter-spacing:-.02em}.kpi .l{color:var(--ink2);font-size:12px}.kpi .s{color:var(--muted);font-size:11px;margin-top:2px}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{padding:6px 8px;border-bottom:1px solid var(--grid);text-align:left;vertical-align:top}
th{color:var(--ink2);font-weight:600;font-size:12px;white-space:nowrap;position:sticky;top:0;background:var(--surface)}td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.tw{overflow:auto;max-height:560px}
.empty{color:var(--muted);font-size:12px}.sales-chart{display:block;width:100%;height:auto;max-height:280px}.sales-chart text{fill:var(--muted);font-size:11px}.chart-grid{stroke:var(--grid)}
.barrow{display:grid;grid-template-columns:175px minmax(60px,1fr) 128px;gap:8px;align-items:center;margin:5px 0;font-size:12px}.barlabel{color:var(--ink2)}.track{background:var(--ghost);height:18px;border-radius:0 4px 4px 0}.fill{height:100%;background:var(--s1);border-radius:0 4px 4px 0}.barvalue{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.barvalue small{color:var(--muted)}
.callout{background:var(--ghost);border-radius:8px;padding:10px 12px;font-size:12px;color:var(--ink2)}.footer{margin:16px 0 32px}
@media(max-width:900px){.g4{grid-template-columns:repeat(2,minmax(0,1fr))}.g2{grid-template-columns:1fr}.barrow{grid-template-columns:135px minmax(50px,1fr) 115px}}
@media(max-width:560px){header{padding:10px 14px}main{padding:14px}.g4{grid-template-columns:1fr}.barrow{grid-template-columns:1fr 1fr}.barlabel{grid-column:1/-1}.barvalue{font-size:11px}}
@media print{header{position:static}nav,.btn{display:none}article[hidden]{display:block!important}.card{break-inside:avoid}.tw{max-height:none}.grid{break-inside:auto}}
"""


SCRIPT = """
const ids=['october','oct31_nov8'];
function selectPeriod(id, updateHash=true){
  if(!ids.includes(id)) id='october';
  ids.forEach(key=>{document.getElementById('panel-'+key).hidden=key!==id;document.querySelector('[data-period="'+key+'"]').setAttribute('aria-selected',String(key===id));});
  if(updateHash) history.replaceState(null,'','#'+id);
  window.scrollTo(0,0);
}
document.querySelectorAll('[data-period]').forEach(b=>b.addEventListener('click',()=>selectPeriod(b.dataset.period)));
document.getElementById('theme').addEventListener('click',()=>{const root=document.documentElement;root.dataset.theme=root.dataset.theme==='dark'?'light':'dark';});
window.addEventListener('hashchange',()=>selectPeriod(location.hash.slice(1),false));
selectPeriod(location.hash.slice(1),false);
window.addEventListener('load',()=>window.scrollTo(0,0));
"""


def make_html(periods, generated_at):
    sections = []
    for p in periods:
        orders = p["orders"]
        net = sum(money(o["net"]) for o in orders)
        big_only = [o for o in orders if o["big_only"]]
        signal_linked = [o for o in orders if not o["big_only"]]
        share = 100 * net / p["us_net"] if p["us_net"] else 0
        cards = [
            ("B2B cohort net sales", dollars(net), f'{share:.1f}% of US net sales ({dollars(p["us_net"])})'),
            ("Orders of $3,000+", f'{len(orders):,}', 'All US Shopify purchasers meeting the threshold'),
            ("With a B2B signal", f'{len(signal_linked):,}', dollars(sum(money(o["net"]) for o in signal_linked)) + ' net sales'),
            ("Without a B2B signal", f'{len(big_only):,}', dollars(sum(money(o["net"]) for o in big_only)) + ' net sales')]
        card_html = ''.join(f'<div class="card kpi"><div class="l">{escape(label)}</div><div class="v">{escape(value)}</div><div class="s">{escape(sub)}</div></div>'
                            for label, value, sub in cards)
        breakdowns = []
        for kind, title in (("lists", "Lists"), ("segments", "Segments"),
                            ("flows", "Flows received"), ("forms", "Forms submitted"),
                            ("coupons", "Coupons used on order")):
            rows = [(x["name"], f'{x["orders"]:,}', dollars(x["sales"])) for x in p["groups"][kind]]
            breakdowns.append(f'<section class="card"><h2>{title}</h2><div class="note">Orders may appear in multiple signal rows.</div>'
                              + table(["Signal", "Orders", "Associated net sales"], rows, numeric_columns=(1, 2)) + '</section>')
        email_sections = []
        for kind, title in (("flow_emails", "Flow emails"), ("campaign_emails", "B2B campaign emails")):
            rows = [(f'{e["flow"]} · {e["name"]}' if kind == "flow_emails" else e["name"],
                     f'{e["recipients"]:,}', f'{e["conversions"]:,.1f}', dollars(e["klaviyo_value"]))
                    for e in p[kind]]
            email_sections.append(f'<section class="card"><h2>{title}</h2>' + table(
                ["Email", "Recipients", "Klaviyo conversions", "Klaviyo attributed value"], rows,
                "No matching B2B email report rows for this period.", numeric_columns=(1, 2, 3)) + '</section>')
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
        sections.append(f'''<article id="panel-{p['id']}" role="tabpanel" hidden><div class="eyebrow">{escape(p['start'])} through {escape(p['end'])}</div>
          <h2 class="period-title">{escape(p['label'])}</h2><div class="grid g4">{card_html}</div>
          <div class="grid g2" style="margin-top:16px"><section class="card"><h2>B2B cohort net sales by day</h2><div class="note">US Shopify orders with at least $3,000 net sales</div>{daily_chart(orders, p['start'], p['end'])}</section>
          <section class="card"><h2>Signals among $3,000+ orders (overlapping)</h2><div class="note">Only threshold-qualifying orders appear here. A purchaser may match several signals.</div>{signal_bars(orders)}</section></div>
          <div class="callout" style="margin-top:16px">Coupon rows below combine one time codes by recognizable family; TRD* cannot be assigned to a verified coupon key.</div>
          <div class="grid g2" style="margin-top:16px">{''.join(breakdowns)}</div>
          <div class="card" style="margin-top:16px"><h2>Email activity context</h2><div class="note">Klaviyo's email report includes conversions of all order sizes. These figures are context only; they are not filtered to the $3,000+ B2B cohort and must not be added to BigQuery B2B net sales.</div></div>
          <div class="grid g2" style="margin-top:16px">{''.join(email_sections)}</div>
          <section class="card" style="margin-top:16px"><h2>Top 25 B2B orders by net sales</h2><div class="note">Biggest orders first; the qualifying signals explain why each order counts.</div>
          {table(['#', 'Order', 'Date', 'Net sales', 'Qualifying signals'], top, numeric_columns=(0, 3))}</section></article>''')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <title>B2B sales snapshot · October and Oct 31–Nov 8 2026</title><style>{STYLE}</style></head>
      <body><header><div class="top"><div><h1>B2B Sales Snapshot</h1><div class="sub">October 2026 and Oct 31 – Nov 8 2026 · Klaviyo + BigQuery · generated {escape(generated_at)} America/Phoenix</div></div>
      <button class="btn" id="theme" type="button">Dark / light</button></div>
      <nav role="tablist" aria-label="Reporting period"><button type="button" role="tab" data-period="october" aria-selected="true">October 2026</button><button type="button" role="tab" data-period="oct31_nov8" aria-selected="false">Oct 31 – Nov 8</button></nav></header>
      <main>{''.join(sections)}<div class="footer callout"><strong>Definition and limits.</strong> Every B2B sale in this report is a US Shopify order with at least $3,000 in BigQuery net sales. All such purchasers count, including those with no B2B list, segment, flow, form, or coupon signal. Signal tables describe only these threshold-qualifying orders. CRTRA10 is excluded from the coupon signal. Current list/segment membership and historical email/form signals may postdate the order; association is not proof of causation. BigQuery net sales include later order adjustments recorded within each reporting window. Samples are excluded. October 31 appears in both views by design. No customer names, contact information, or addresses appear in this report.</div></main>
      <script>{SCRIPT}</script></body></html>'''


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
