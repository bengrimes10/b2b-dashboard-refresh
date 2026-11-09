#!/usr/bin/env python3
"""Refresh the local B2B dashboard using read-only Klaviyo and BigQuery access.

The Klaviyo key is read from KLAVIYO_PRIVATE_API_KEY. Google uses ADC. Customer
records and the generated HTML are written only to git-ignored local paths.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import requests
from google.cloud import bigquery

ROOT = Path(__file__).resolve().parent.parent
START = date(2026, 10, 31)
# Verified against BigQuery time travel at 2026-11-09 04:00 Phoenix. See docs/validation_2026-11-09.md.
ACCEPTED_LATE_ADJUSTMENTS = {"2026-11-08": (300228.94, 287641.32)}
PROJECT = "hd-global-analytics-platform"
BILLING_PROJECT = os.getenv("BIGQUERY_PROJECT_ID", "db-project-30840")
TZ = ZoneInfo("America/Phoenix")
FLOW_IDS = "XA5MGj SZMWtA SVNQ6Y RjvnXL QTXBNv R77Xza T8tDXU TgGSgX ThRrHh XV5Jxt XcCt2k Y89wS2 YiK4az SWgfrf YnCsYM".split()
CORE_LISTS = "RArqFN SuJXq5 XYuXP5 S3uABq TM29XF UeKxx2 UwUddt".split()
CORE_SEGMENTS = "UtP84e U2Z8YK VExknn Ycbxb4 R77LxD Yb9xBp UMxDa4".split()
EXT_LISTS = "Rh4564 VGrNiu".split()
EXT_SEGMENTS = "SmZ4HF XsSRLS".split()
FORMS = "Uxsw3c VpZjF6 Rjbv4E Sx2BJq U5HrsT Yewkfv VBYJhK VDnyVy".split()
FLOW_NAMES = {
    "XA5MGj": "Ex-Trade Members - Welcome Series",
    "SZMWtA": "Ex-Trade Members (TEC) - Welcome Series",
    "SVNQ6Y": "B2B - Welcome Series",
    "RjvnXL": "B2B Masterlist - Re-Entry Flow",
}
LIST_NAMES = dict(zip(CORE_LISTS, [
    "Trade - Business Interest Only", "B2B Masterlist", "Commercial - Website RAQ",
    "B2B Masterlist - Inactive", "Commercial/B2B Internal Additions",
    "Commercial/B2B Inquiries from Website", "Old Commercial Inquiries"]))
SEGMENT_NAMES = dict(zip(CORE_SEGMENTS, [
    "Trade TEC Results", "Official Master List (All)",
    "Official Master List (Can Receive Mktg)", "Trade Membership Level",
    "Trade Silver", "Trade Coupon Used Reissue", "Old Commercial Inquiries"]))
FLOW_NAMES.update(dict(zip(FLOW_IDS[4:], [
    "Trade Membership Assignment - First Upload from Listrak",
    "Trade Membership Assignment - First Upload from Listrak (variant)",
    "Trade - Initial Application Thanks + Membership Assignment",
    "B2B-Like Customers - Invite to Trade/Commercial",
    "Trade - New Tier Level - Silver",
    "Trade Membership Assignment - First Upload from Listrak (variant)",
    "Trade - Membership Level Communication", "Trade - TEC Accepted/Not Accepted",
    "Trade - Coupon Used - Reissue New", "Old Commercial Inquiries",
    "Commercial Pricing Inquiry"])))
FORM_NAMES = dict(zip(FORMS, [
    "Shopify - Trade Application Form", "Shopify - Commercial Inquiry Form",
    "Invited Trade Pop Up", "Trade Reminder Flyout", "Trade Application Form",
    "Commercial Inquiry Form", "B2B Audience - News Pop In",
    "B2B - President's Day Extra 5% Off $750 Email Sign Up"]))
STATISTICS = ["recipients", "delivered", "opens_unique", "clicks_unique",
              "conversion_uniques", "conversion_value", "unsubscribe_uniques",
              "bounced", "spam_complaints"]


def money(value):
    return round(float(value or 0), 2)


def day_label(d: date):
    return d.strftime("%b %-d") if os.name != "nt" else d.strftime("%b %#d")


def days_through(end: date):
    return [START + timedelta(days=i) for i in range((end - START).days + 1)]


class Klaviyo:
    def __init__(self, key: str):
        self.key = key
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Klaviyo-API-Key {key}",
                                     "revision": "2026-10-15",
                                     "Accept": "application/vnd.api+json",
                                     "Content-Type": "application/vnd.api+json"})

    def request(self, method: str, path: str, **kwargs):
        url = path if path.startswith("https://a.klaviyo.com/api/") else "https://a.klaviyo.com/api/" + path.lstrip("/")
        if urlparse(url).hostname != "a.klaviyo.com":
            raise ValueError("Unexpected Klaviyo pagination host")
        request_timeout = kwargs.pop("timeout", 90)
        for attempt in range(6):
            response = self.session.request(method, url, timeout=request_timeout, **kwargs)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 5:
                time.sleep(min(float(response.headers.get("Retry-After", 2 ** attempt)), 60))
                continue
            if not response.ok:
                raise RuntimeError(f"Klaviyo {method} {urlparse(url).path}: HTTP {response.status_code}: {response.text[:600]}")
            return response.json()
        raise RuntimeError("Klaviyo retries exhausted")

    def pages(self, path: str, params=None):
        next_url = path
        first = True
        while next_url:
            payload = self.request("GET", next_url, params=params if first else None)
            yield from payload.get("data", [])
            next_url = (payload.get("links") or {}).get("next")
            first = False

    def report(self, kind: str, attributes: dict):
        rows = []
        cursor = None
        while True:
            payload = self.request("POST", kind + "-values-reports",
                                   params={"page_cursor": cursor} if cursor else None,
                                   json={"data": {"type": kind + "-values-report", "attributes": attributes}})
            rows.extend(payload["data"]["attributes"].get("results", []))
            next_link = (payload.get("links") or {}).get("next")
            if not next_link:
                return rows
            cursor = parse_qs(urlparse(next_link).query).get("page_cursor", [None])[0]
            if not cursor:
                raise RuntimeError("Klaviyo report pagination link has no cursor")

    def events(self, metric: str, start: datetime, end: datetime, property_name=None, property_value=None):
        if property_name is not None:
            if property_name not in ("$flow", "form_id"):
                raise ValueError("Unsupported event property filter")
        window_start = start
        while window_start < end:
            window_end = min(window_start + timedelta(days=360), end)
            parts = [f'equals(metric_id,"{metric}")',
                     f'greater-or-equal(datetime,{window_start.isoformat()})',
                     f'less-than(datetime,{window_end.isoformat()})']
            if property_name is not None:
                parts.append(f'equals(properties.{property_name},{json.dumps(property_value)})')
            filt = 'and(' + ','.join(parts) + ')'
            fields = ("datetime" if property_name else
                      "datetime,event_properties.$value,event_properties.$extra.name,"
                      "event_properties.Discount Codes" if metric == "WvaquY" else
                      "datetime,event_properties.CouponKey,event_properties.coupon_key")
            yield from self.pages("events", {"filter": filt, "page[size]": 1000,
                                             "fields[event]": fields,
                                             "sort": "datetime"})
            window_start = window_end


def membership(api: Klaviyo, ids: list[str], kind: str):
    found = defaultdict(set)
    for item_id in ids:
        for profile in api.pages(f"{kind}/{item_id}/profiles", {"page[size]": 100,
                                                                  "fields[profile]": "id"}):
            found[profile["id"]].add(item_id)
    return found


def all_memberships(api: Klaviyo):
    cache_path = ROOT / "data/cache/memberships.json"
    if cache_path.exists():
        saved = json.loads(cache_path.read_text(encoding="utf-8"))
        if datetime.now(timezone.utc) - datetime.fromisoformat(saved["created_at"]) < timedelta(hours=1):
            print("Using recent local audience cache", flush=True)
            return {group: {pid: set(ids) for pid, ids in members.items()}
                    for group, members in saved["groups"].items()}
    groups = {"core_lists": ("lists", CORE_LISTS),
              "core_segments": ("segments", CORE_SEGMENTS),
              "ext_lists": ("lists", EXT_LISTS),
              "ext_segments": ("segments", EXT_SEGMENTS)}
    tasks = [(group, kind, audience_id) for group, (kind, ids) in groups.items()
             for audience_id in ids]

    def fetch(task):
        group, kind, audience_id = task
        worker = Klaviyo(api.key)
        members = {profile["id"] for profile in worker.pages(
            f"{kind}/{audience_id}/profiles",
            {"page[size]": 100, "fields[profile]": "id"})}
        return group, audience_id, members

    result = {group: defaultdict(set) for group in groups}
    with ThreadPoolExecutor(max_workers=10) as pool:
        for group, audience_id, members in pool.map(fetch, tasks):
            for profile_id in members:
                result[group][profile_id].add(audience_id)
            print(f"Audience {group} {audience_id}: {len(members)} members", flush=True)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(),
                                      "groups": {g: {p: sorted(ids) for p, ids in members.items()}
                                                 for g, members in result.items()}},
                                     separators=(",", ":")), encoding="utf-8")
    return result


def signal_cache(api: Klaviyo, now: datetime):
    path = ROOT / "data/cache/signals.json"
    cache = json.loads(path.read_text()) if path.exists() else {"flow": {}, "form": {}, "through": None}
    since = (datetime.fromisoformat(cache["through"]) - timedelta(hours=6)
             if cache.get("through") else datetime(2023, 11, 1, tzinfo=timezone.utc))
    tasks = [(metric, target, property_name, value)
             for metric, target, property_name, values in (
                 ("YiWaij", "flow", "$flow", FLOW_IDS),
                 ("XDVFye", "form", "form_id", FORMS),
                 ("Ts4RUD", "form", "form_id", FORMS)) for value in values]

    def fetch(task):
        metric, target, property_name, value = task
        worker = Klaviyo(api.key)
        profiles = set()
        for event in worker.events(metric, since, now, property_name, value):
            profile = ((event.get("relationships") or {}).get("profile") or {}).get("data") or {}
            if profile.get("id"):
                profiles.add(profile["id"])
        return metric, target, value, profiles

    with ThreadPoolExecutor(max_workers=8) as pool:
        for metric, target, value, profiles in pool.map(fetch, tasks):
            for pid in profiles:
                cache[target].setdefault(pid, [])
                if value not in cache[target][pid]:
                    cache[target][pid].append(value)
            print(f"Signal {metric} {value}: {len(profiles)} profiles", flush=True)
    cache["through"] = now.isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, separators=(",", ":")))
    return cache


def code_strings(props: dict):
    raw = props.get("Discount Codes") or props.get("$extra", {}).get("discount_codes") or []
    if isinstance(raw, str):
        return [raw]
    return [x if isinstance(x, str) else str(x.get("code", "")) for x in raw]


def order_flags(api: Klaviyo, start: datetime, end: datetime, lists, segments, ext_lists, ext_segments, cache):
    flags = {}
    codes = defaultdict(list)
    details = {}
    seen = eligible = 0
    for event in api.events("WvaquY", start, end):
        props = event.get("attributes", {}).get("event_properties") or {}
        if money(props.get("$value")) <= 0:
            continue
        seen += 1
        name = str((props.get("$extra") or {}).get("name") or "")
        if not re.fullmatch(r"US\d{6,}", name):
            continue
        eligible += 1
        pid = (((event.get("relationships") or {}).get("profile") or {}).get("data") or {}).get("id")
        cs = code_strings(props)
        letters = set()
        if pid in lists: letters.add("l")
        if pid in segments: letters.add("s")
        if pid in cache["flow"]: letters.add("f")
        if pid in cache["form"]: letters.add("m")
        if pid in ext_lists or pid in ext_segments: letters.add("x")
        if any(re.search(r"trade|trd|b2b", c, re.I) and c.upper() != "CRTRA10" for c in cs):
            letters.add("c")
        if letters:
            flags.setdefault(name, set()).update(letters)
            detail = details.setdefault(name, {"lists": set(), "segs": set(),
                                               "flows": set(), "forms": set(), "coupon": ""})
            detail["lists"].update(LIST_NAMES.get(x, x) for x in lists.get(pid, ()))
            detail["segs"].update(SEGMENT_NAMES.get(x, x) for x in segments.get(pid, ()))
            detail["flows"].update(FLOW_NAMES.get(x, x) for x in cache["flow"].get(pid, ()))
            detail["forms"].update(FORM_NAMES.get(x, x) for x in cache["form"].get(pid, ()))
            if "c" in letters:
                detail["coupon"] = next((c for c in cs if re.search(r"trade|trd|b2b", c, re.I)
                                           and c.upper() != "CRTRA10"), "")
        codes[name].extend(cs)
    if seen and eligible / seen < .9:
        raise RuntimeError(f"Only {eligible}/{seen} positive Klaviyo orders had US order names")
    private_details = {name: {key: sorted(value) if isinstance(value, set) else value
                              for key, value in detail.items()} for name, detail in details.items()}
    return ([{"order_name": n, "fl": "".join(sorted(v))} for n, v in flags.items()],
            codes, eligible, private_details)


def query(client, sql: str, end: date, flags=None):
    parameters = [bigquery.ScalarQueryParameter("end", "DATE", end)]
    if flags is not None:
        parameters.append(bigquery.ScalarQueryParameter("flags_json", "STRING", json.dumps(flags)))
    cfg = bigquery.QueryJobConfig(query_parameters=parameters)
    return [dict(row.items()) for row in client.query(sql, job_config=cfg, location="EU").result()]


TOP_SQL = f"""
WITH nb AS (
 SELECT date_ref d, SUM(net_sales_usd) net, SUM(gross_sales_usd) gross
 FROM `{PROJECT}.mart_ecom_tracker.net_sales_bookings`
 WHERE country='US' AND date_ref BETWEEN DATE '2026-10-31' AND @end GROUP BY 1),
oi AS (
 SELECT DATE(order_created_at) d,
 COUNT(DISTINCT IF(DataSource='Shopify',order_name,CAST(order_id AS STRING))) orders,
 SUM(net_revenue_usd) oi_net, SUM(estimated_cost_usd) est_cost
 FROM `{PROJECT}.mart_ecom_tracker.order_items`
 WHERE country='US' AND order_type='Order' AND (is_original_version IS NULL OR is_original_version=1)
 AND order_created_at BETWEEN DATE '2026-10-31' AND @end GROUP BY 1)
SELECT nb.d, ROUND(nb.net,2) net, ROUND(nb.gross,2) gross, oi.orders,
 ROUND(oi.oi_net,2) oi, ROUND(oi.est_cost,2) cost
FROM nb LEFT JOIN oi USING(d) ORDER BY d
"""

COHORT_BASE = f"""
WITH c AS (
 SELECT JSON_VALUE(x,'$.order_name') order_name, JSON_VALUE(x,'$.fl') fl
 FROM UNNEST(JSON_QUERY_ARRAY(@flags_json)) x),
shop AS (
 SELECT order_name, MIN(DATE(order_created_at)) d, SUM(net_revenue_usd) oi_net,
 SUM(estimated_cost_usd) est_cost, SUM(line_item_quantity) units,
 ANY_VALUE(LOWER(TRIM(email))) email, ANY_VALUE(first_name) fn, ANY_VALUE(last_name) ln,
 ANY_VALUE(shipping_address_city) city, ANY_VALUE(shipping_address_province) st,
 ARRAY_TO_STRING(ARRAY_AGG(DISTINCT product_name IGNORE NULLS LIMIT 2),' | ') prods
 FROM `{PROJECT}.mart_ecom_tracker.order_items`
 WHERE country='US' AND order_type='Order' AND DataSource='Shopify'
 AND (is_original_version IS NULL OR is_original_version=1)
 AND order_created_at BETWEEN DATE '2026-10-31' AND @end GROUP BY 1),
nbs AS (
 SELECT order_name, SUM(net_sales_usd) net_sales
 FROM `{PROJECT}.mart_ecom_tracker.net_sales_bookings`
 WHERE country='US' AND date_ref BETWEEN DATE '2026-10-31' AND @end GROUP BY 1),
big AS (SELECT n.order_name FROM nbs n JOIN shop s USING(order_name) WHERE n.net_sales>=3000),
ca AS (SELECT COALESCE(c.order_name,b.order_name) order_name, IFNULL(c.fl,'') fl,
 b.order_name IS NOT NULL is_big FROM c FULL JOIN big b USING(order_name)),
c2 AS (SELECT *, (REGEXP_CONTAINS(fl,r'[lsfmc]') OR is_big) core,
 REGEXP_CONTAINS(fl,r'l') f_list, REGEXP_CONTAINS(fl,r's') f_seg,
 REGEXP_CONTAINS(fl,r'f') f_flow, REGEXP_CONTAINS(fl,r'm') f_form,
 REGEXP_CONTAINS(fl,r'c') f_coupon,
 (is_big AND NOT REGEXP_CONTAINS(fl,r'[lsfmc]')) big_only FROM ca)
"""
COHORT_SQL = COHORT_BASE + """
SELECT shop.d, IF(c2.core,'core','ext_only') tier, COUNT(*) orders,
 ROUND(SUM(nbs.net_sales),2) net_sales, ROUND(SUM(shop.est_cost),2) est_cost,
 COUNTIF(f_list) n_list, COUNTIF(f_seg) n_seg, COUNTIF(f_flow) n_flow,
 COUNTIF(f_form) n_form, COUNTIF(f_coupon) n_coupon,
 ROUND(SUM(IF(f_list,nbs.net_sales,0)),2) s_list,
 ROUND(SUM(IF(f_seg,nbs.net_sales,0)),2) s_seg,
 ROUND(SUM(IF(f_flow,nbs.net_sales,0)),2) s_flow,
 ROUND(SUM(IF(f_form,nbs.net_sales,0)),2) s_form,
 ROUND(SUM(IF(f_coupon,nbs.net_sales,0)),2) s_coupon,
 COUNTIF(is_big) n_big, ROUND(SUM(IF(is_big,nbs.net_sales,0)),2) s_big,
 COUNTIF(big_only) n_bigonly, ROUND(SUM(IF(big_only,nbs.net_sales,0)),2) s_bigonly
FROM c2 JOIN shop USING(order_name) LEFT JOIN nbs USING(order_name)
GROUP BY 1,2 ORDER BY 1,2
"""
BIG_SQL = COHORT_BASE + """
SELECT ca.order_name o, CAST(shop.d AS STRING) d, ROUND(nbs.net_sales,2) net,
 shop.units, shop.email em, shop.fn, shop.ln, shop.city, shop.st, shop.prods,
 CONCAT(REGEXP_REPLACE(ca.fl,'x',''),IF(ca.is_big,'b','')) sig
FROM c2 ca JOIN shop USING(order_name) JOIN nbs USING(order_name)
WHERE ca.core AND nbs.net_sales>0 ORDER BY nbs.net_sales DESC LIMIT 400
"""
LEADS_SQL = f"""
WITH orders AS (
 SELECT LOWER(TRIM(email)) email,
 IF(DataSource='Shopify',order_name,CAST(order_id AS STRING)) order_key,
 MIN(DATE(order_created_at)) d,
 SUM(net_revenue_usd) net, ANY_VALUE(first_name) fn, ANY_VALUE(last_name) ln,
 ANY_VALUE(phone_number) phone, ANY_VALUE(shipping_address_city) city,
 ANY_VALUE(shipping_address_province) st
 FROM `{PROJECT}.mart_ecom_tracker.order_items`
 WHERE country='US' AND order_type='Order' AND (is_original_version IS NULL OR is_original_version=1)
 AND order_created_at >= DATE_SUB(@end, INTERVAL 36 MONTH)
 AND order_created_at <= @end AND email IS NOT NULL AND TRIM(email)!=''
 GROUP BY 1,2 HAVING net>0),
leads AS (
 SELECT email, ANY_VALUE(fn) fn, ANY_VALUE(ln) ln, ANY_VALUE(phone) phone,
 ANY_VALUE(city) city, ANY_VALUE(st) st, COUNT(*) orders, SUM(net) ltv,
 AVG(net) aov, MAX(net) max_order, MIN(d) first_order, MAX(d) last_order
 FROM orders GROUP BY email)
SELECT * FROM leads WHERE max_order>=2000 OR (orders>=3 AND aov>=600)
ORDER BY ltv DESC LIMIT 400
"""


def flow_report(api, live, end_dt):
    attrs = {"statistics": STATISTICS, "timeframe": {"start": "2026-10-31T00:00:00-07:00",
              "end": end_dt.isoformat()}, "conversion_metric_id": "WvaquY",
             "group_by": ["flow_id", "flow_message_id", "flow_message_name"],
             "filter": 'contains-any(flow_id,' + json.dumps(list(live), separators=(",", ":")) + ')'}
    rows = api.report("flow", attrs)
    msgs = []
    for row in rows:
        g, s = row["groupings"], row["statistics"]
        msgs.append({"f": g["flow_id"], "m": g.get("flow_message_name", "Email"),
                     "rec": int(s.get("recipients", 0)), "del": int(s.get("delivered", 0)),
                     "opens": int(s.get("opens_unique", 0)), "clicks": int(s.get("clicks_unique", 0)),
                     "conv": int(s.get("conversion_uniques", 0)), "rev": money(s.get("conversion_value")),
                     "unsub": int(s.get("unsubscribe_uniques", 0)), "bounced": int(s.get("bounced", 0)),
                     "spam": int(s.get("spam_complaints", 0))})
    for fid in live:
        if not any(row["f"] == fid for row in msgs):
            msgs.append({"f": fid, "m": "No sends", "rec": 0, "del": 0,
                         "opens": 0, "clicks": 0, "conv": 0, "rev": 0,
                         "unsub": 0, "bounced": 0, "spam": 0})
    return msgs


def live_flows(api):
    items = api.pages("flows", {"filter": 'equals(status,"live")', "page[size]": 50,
                                 "fields[flow]": "name,status"})
    live = {x["id"]: x["attributes"]["name"] for x in items
            if re.search(r"trade|commercial|b2b", x["attributes"].get("name", ""), re.I)}
    if not live:
        raise RuntimeError("No live B2B flows returned")
    return live


def enrollment(api, live, asof):
    body = {"data": {"type": "metric-aggregate", "attributes": {
        "metric_id": "YiWaij", "measurements": ["unique"], "by": ["$flow"],
        "interval": "day", "timezone": "US/Arizona",
        "filter": ["greater-or-equal(datetime,2026-10-31T00:00:00-07:00)",
                   f"less-than(datetime,{(asof + timedelta(days=1)).isoformat()}T00:00:00-07:00)"]}}}
    payload = api.request("POST", "metric-aggregates", json=body)
    attrs = payload["data"]["attributes"]
    out = {d.isoformat(): {"d": d.isoformat(), **{fid: 0 for fid in live}}
           for d in days_through(asof)}
    for row in attrs.get("data", []):
        fid = row.get("dimensions", [None])[0]
        if fid not in live: continue
        for bucket, value in zip(attrs.get("dates", []), row["measurements"]["unique"]):
            day = datetime.fromisoformat(bucket).astimezone(TZ).date().isoformat()
            if day in out: out[day][fid] = int(value)
    return list(out.values())


def campaign_report(api, end_dt):
    attrs = {"statistics": STATISTICS, "timeframe": {"start": "2026-10-31T00:00:00-07:00",
              "end": end_dt.isoformat()}, "conversion_metric_id": "WvaquY",
             "group_by": ["campaign_id", "campaign_message_id", "campaign_message_name"]}
    result = []
    for row in api.report("campaign", attrs):
        details = row.get("campaign_details") or {}
        info = details.get("attributes") or {}
        name = info.get("name") or row.get("groupings", {}).get("campaign_message_name", "")
        if not re.search(r"trade|commercial|b2b", name, re.I):
            continue
        s = row["statistics"]
        audience = info.get("audiences") or {}
        included = audience.get("included") or []
        result.append({"n": name, "aud": ", ".join(x.get("name", "") for x in included),
                       "sent": str(info.get("send_time") or ""),
                       "rec": int(s.get("recipients", 0)), "dl": int(s.get("delivered", 0)),
                       "opens": int(s.get("opens_unique", 0)),
                       "clicks": int(s.get("clicks_unique", 0)),
                       "conv": int(s.get("conversion_uniques", 0)),
                       "rev": money(s.get("conversion_value")),
                       "unsub": int(s.get("unsubscribe_uniques", 0))})
    return result


def coupon_report(api, start_dt, now, order_codes):
    counts = Counter()
    for event in api.events("Ypku5R", start_dt, now):
        props = event.get("attributes", {}).get("event_properties") or {}
        key = props.get("CouponKey") or props.get("coupon_key")
        if key:
            counts[str(key)] += 1
    names = ["TRADE10", "TRADE15", "TRADE20", "Trade_TRD", "Trade_TRA", "TRD500_Email"]
    names.extend(sorted(n for n in counts if n.upper().startswith("B2B") and n not in names))
    coupons = {}
    for item in api.pages("coupons", {"page[size]": 100}):
        attributes = item.get("attributes") or {}
        key = attributes.get("external_id") or item.get("id")
        coupons[str(key).upper()] = item["id"]
    result = []
    for name in names:
        matched = sum(any(c.upper().startswith(name.upper()) for c in codes)
                      for codes in order_codes.values())
        left = "Unknown"
        coupon_id = coupons.get(name.upper()) or (name if name in ("TRADE10", "TRADE15", "TRADE20") else None)
        if coupon_id:
            try:
                page_size = 5 if name == "TRADE10" else 100
                page = api.request("GET", "coupon-codes", timeout=12,
                                   params={"filter": f'and(equals(coupon.id,"{coupon_id}"),equals(status,"UNASSIGNED"))',
                                           "page[size]": page_size, "fields[coupon-code]": "id"})
                left = f">={page_size}" if (page.get("links") or {}).get("next") else str(len(page.get("data", [])))
            except (requests.RequestException, RuntimeError):
                left = "Unknown"
        result.append({"n": name, "where": "Trade/B2B coupon", "asg": counts[name],
                       "left": left, "used": "Unknown", "matched": matched})
    return result


def build_cohort(rows, dates):
    by_day = {(str(r["d"]), r["tier"]): r for r in rows}
    zero = {k: 0 for k in "o net cost nl ns nf nm nc sl ss sf sm sc nb sb nbo sbo".split()}
    result = []
    for d in dates:
        core, ext = by_day.get((d.isoformat(), "core")), by_day.get((d.isoformat(), "ext_only"))
        entry = dict(zero)
        if core:
            for short, long in {"o":"orders", "net":"net_sales", "cost":"est_cost",
                 "nl":"n_list", "ns":"n_seg", "nf":"n_flow", "nm":"n_form", "nc":"n_coupon",
                 "sl":"s_list", "ss":"s_seg", "sf":"s_flow", "sm":"s_form", "sc":"s_coupon",
                 "nb":"n_big", "sb":"s_big", "nbo":"n_bigonly", "sbo":"s_bigonly"}.items():
                entry[short] = money(core[long]) if short in ("net cost sl ss sf sm sc sb sbo".split()) else int(core[long])
        result.append({"d": d.strftime("%m-%d"), "core": entry,
                       "ext": {"o": int(ext["orders"]), "net": money(ext["net_sales"]),
                               "cost": money(ext["est_cost"])} if ext else {"o": 0, "net": 0, "cost": 0}})
    return result


def check_checkpoint(top):
    history = json.loads((ROOT / "data/history/2026-11-09.json").read_text())
    original = {r["d"]: r["net"] for r in history["D"]["top"]}
    changed = []
    for r in top:
        if r["d"] in original and original[r["d"]]:
            accepted = ACCEPTED_LATE_ADJUSTMENTS.get(r["d"])
            if (accepted and abs(original[r["d"]] - accepted[0]) < .01
                    and abs(r["net"] / accepted[1] - 1) <= .01):
                continue
            pct = 100 * (r["net"] / original[r["d"]] - 1)
            if abs(pct) > 1: changed.append((r["d"], original[r["d"]], r["net"], round(pct, 2)))
    return changed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint-only", action="store_true")
    parser.add_argument("--preview-on-drift", action="store_true",
                        help="Build a private validation preview without saving history when the checkpoint differs")
    parser.add_argument("--asof", type=date.fromisoformat)
    args = parser.parse_args()
    asof = args.asof or datetime.now(TZ).date()
    client = bigquery.Client(project=BILLING_PROJECT)
    top_rows = query(client, TOP_SQL, asof - timedelta(days=1))
    top = [{"d": str(r["d"]), "net": money(r["net"]), "gross": money(r["gross"]),
            "orders": int(r["orders"] or 0), "oi": money(r["oi"]), "cost": money(r["cost"]),
            "b2b": 0, "b2bo": 0} for r in top_rows]
    changes = check_checkpoint(top)
    print("Accepted Nov 8 late adjustment: original $300,228.94; new baseline $287,641.32")
    print("US sales checkpoint changes over 1%:", changes)
    if args.checkpoint_only: return 0
    if not args.preview_on_drift and (ROOT / "data/history" / f"{asof.isoformat()}.json").exists():
        raise RuntimeError("Today's metrics snapshot already exists; use --preview-on-drift for private validation")
    key = os.getenv("KLAVIYO_PRIVATE_API_KEY")
    if not key:
        try:
            import keyring
            key = keyring.get_password("selectblinds-b2b-dashboard", "klaviyo-readonly")
        except ImportError:
            pass
    if not key:
        raise RuntimeError("KLAVIYO_PRIVATE_API_KEY is missing. See README.md for local setup.")
    api = Klaviyo(key)
    live = live_flows(api)
    start_dt = datetime(2026, 10, 31, tzinfo=TZ).astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    audiences = all_memberships(api)
    list_ids, segment_ids = audiences["core_lists"], audiences["core_segments"]
    ext_list_ids, ext_segment_ids = audiences["ext_lists"], audiences["ext_segments"]
    cache = signal_cache(api, now)
    flags, codes, eligible, details = order_flags(api, start_dt, now, list_ids, segment_ids,
                                                  ext_list_ids, ext_segment_ids, cache)
    print("Positive US order events", eligible, "flagged orders", len(flags), flush=True)
    dates = [date.fromisoformat(r["d"]) for r in top]
    cohort = build_cohort(query(client, COHORT_SQL, dates[-1], flags), dates)
    print("BigQuery cohort matched", sum(x["core"]["o"] + x["ext"]["o"] for x in cohort), flush=True)
    big_rows = query(client, BIG_SQL, dates[-1], flags)
    big = [{k: (money(v) if k == "net" else v) for k, v in row.items()} |
           {"det": details.get(row["o"], {})}
           for row in big_rows]
    leads_rows = query(client, LEADS_SQL, dates[-1])
    leads = [{"email": r["email"], "fn": r["fn"] or "", "ln": r["ln"] or "",
              "phone": r["phone"] or "", "city": r["city"] or "", "st": r["st"] or "",
              "orders": int(r["orders"]), "ltv": money(r["ltv"]), "aov": money(r["aov"]),
              "max": money(r["max_order"]), "first": str(r["first_order"]),
              "last": str(r["last_order"])} for r in leads_rows]
    msgs = flow_report(api, live, datetime.now(TZ))
    enr = enrollment(api, live, asof)
    camps = campaign_report(api, datetime.now(TZ))
    coupons = coupon_report(api, start_dt, now, codes)
    issues = []
    for cp in coupons:
        if cp["n"] in ("TRADE10", "TRADE15", "TRADE20") and cp["left"] == "0":
            issues.append({"sev": "HIGH", "flow": "B2B coupons", "issue": cp["n"] + " code pool empty",
                           "evidence": "No unassigned codes returned for this coupon.",
                           "action": "Top up the coupon pool before sending more email."})
    if any("CRTRA10" in (c.upper() for c in row) for row in codes.values()):
        issues.append({"sev": "INFO", "flow": "B2B sales tracking",
                       "issue": "CRTRA10 remains outside the B2B coupon signal",
                       "evidence": "This code appears on orders, but its trade status is unconfirmed.",
                       "action": "Confirm whether CRTRA10 is a trade code."})
    issues.append({"sev": "INFO", "flow": "Attribution",
                   "issue": "Order-level attribution split unavailable",
                   "evidence": "The public event and reporting pulls used here do not reproduce the prior K-SQL attribution table.",
                   "action": "Use an approved Klaviyo export or warehouse attribution source to restore the split."})
    last_day = dates[-1]
    meta = {"asof": asof.isoformat(), "asof_long": asof.strftime("%b %d %Y").replace(" 0", " "),
            "range": f"Oct 31 – {day_label(last_day)}",
            "range_long": f"Oct 31 – {day_label(last_day)} 2026",
            "utc_range": f"Oct 31 – {day_label(asof)} UTC",
            "klaviyo_qualifying": eligible,
            "matched": sum(x["core"]["o"] + x["ext"]["o"] for x in cohort)}
    flows = [{"id": fid, "name": name, "short": name.split(" - ")[0],
              "c": f"--s{(i % 5) + 1}"} for i, (fid, name) in enumerate(live.items())]
    data = {"D": {"flows": flows, "msgs": msgs, "enr": enr, "top": top,
                  "issues": issues, "coupons": [], "leads": leads, "big": big,
                  "meta": meta},
            "cohort": cohort,
            "NA": {"attr": None, "camp": camps, "cp": coupons,
                   "sends": {"flows": sum(x["rec"] for x in msgs),
                             "camps": sum(x["rec"] for x in camps)}}}
    if changes and not args.preview_on_drift:
        raise RuntimeError("Historical sales changed by more than 1%; checkpoint review required before publishing")
    latest = ROOT / ("out/validation_data.json" if args.preview_on_drift else "data/latest.json")
    latest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    output = ROOT / ("out/B2B_Dashboard_validation.html" if args.preview_on_drift else "out/B2B_Dashboard.html")
    subprocess.run([sys.executable, str(ROOT / "scripts/build_dashboard.py"), str(latest), str(output)],
                   check=True)
    if args.preview_on_drift:
        print("Private validation preview only; no history snapshot or daily publication")
    else:
        safe = json.loads(json.dumps(data))
        safe["D"]["leads"] = []
        safe["D"]["big"] = []
        history = ROOT / "data/history" / f"{asof.isoformat()}.json"
        if history.exists():
            raise RuntimeError(f"Metrics snapshot already exists; refusing to overwrite {history}")
        history.write_text(json.dumps(safe, ensure_ascii=False), encoding="utf-8")
        print("Metrics snapshot:", history)
    print("Private dashboard:", output)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"Refresh stopped: {exc}", file=sys.stderr)
        sys.exit(1)
