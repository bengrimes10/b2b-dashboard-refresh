"""Zendesk commercial/trade contact cohorts matched to later Shopify orders.

Only aggregate rows leave BigQuery. The HTML and latest data remain local/private.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal


SQL = r"""
WITH freshness AS (
  SELECT MAX(_airbyte_extracted_at) data_through
  FROM `hd-global-analytics-platform.mart_ecom_tracker.tickets`
  WHERE brand_name = 'Select Blinds (US)'
    AND LOWER(recipient) IN ('commercial@selectblinds.com', 'trade@selectblinds.com')
), tickets AS (
  SELECT LOWER(recipient) mailbox,
    LOWER(TRIM(JSON_VALUE(via, '$.source.from.address'))) email,
    created_at contact_at,
    DATE(created_at, 'America/Phoenix') contact_date,
    COALESCE(SAFE_CAST(replies AS INT64), 0) replies,
    business_reply_time_in_minutes reply_minutes
  FROM `hd-global-analytics-platform.mart_ecom_tracker.tickets`
  WHERE brand_name = 'Select Blinds (US)'
    AND LOWER(recipient) IN ('commercial@selectblinds.com', 'trade@selectblinds.com')
    AND channel = 'email' AND requester_id = submitter_id
    AND DATE(created_at, 'America/Phoenix') < @asof
    AND REGEXP_CONTAINS(LOWER(TRIM(JSON_VALUE(via, '$.source.from.address'))), r'^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$')
    AND SPLIT(LOWER(TRIM(JSON_VALUE(via, '$.source.from.address'))), '@')[SAFE_OFFSET(1)]
      NOT IN ('selectblinds.com', 'hdecom.com', 'hunterdouglas.com')
), labeled AS (
  SELECT t.*, 'month' grain, FORMAT_DATE('%Y-%m', contact_date) period FROM tickets t
  UNION ALL SELECT t.*, 'before' grain, 'Oct 28–Nov 2' period FROM tickets t
    WHERE contact_date BETWEEN '2026-10-28' AND '2026-11-02'
  UNION ALL SELECT t.*, 'after' grain, 'Nov 3–8' period FROM tickets t
    WHERE contact_date BETWEEN '2026-11-03' AND '2026-11-08'
  UNION ALL SELECT t.*, 'before_3d' grain, 'Oct 31–Nov 2' period FROM tickets t
    WHERE contact_date BETWEEN '2026-10-31' AND '2026-11-02'
  UNION ALL SELECT t.*, 'after_3d' grain, 'Nov 3–5' period FROM tickets t
    WHERE contact_date BETWEEN '2026-11-03' AND '2026-11-05'
  UNION ALL SELECT t.*, 'post_day' grain, CAST(contact_date AS STRING) period FROM tickets t
    WHERE contact_date >= '2026-11-03'
  UNION ALL SELECT t.* REPLACE('both' AS mailbox), 'mtd' grain,
    FORMAT_DATE('%Y-%m', contact_date) period FROM tickets t
    WHERE contact_date >= DATE_TRUNC(@asof, MONTH)
), contacts AS (
  SELECT grain, period, mailbox, email, MIN(contact_at) first_contact,
    COUNT(*) tickets, SUM(replies) replies,
    COUNTIF(replies > 0) replied_tickets,
    ARRAY_AGG(IF(replies > 0, reply_minutes, NULL) IGNORE NULLS
      ORDER BY contact_at LIMIT 1)[SAFE_OFFSET(0)] reply_minutes
  FROM labeled GROUP BY 1,2,3,4
), orders AS (
  SELECT LOWER(TRIM(email)) email, order_name,
    MIN(order_created_at_timestamp) placed_at
  FROM `hd-global-analytics-platform.mart_ecom_tracker.order_items`
  WHERE country = 'US' AND order_type = 'Order' AND DataSource = 'Shopify'
    AND (is_original_version IS NULL OR is_original_version = 1)
    AND order_created_at >= '2025-06-23' AND order_created_at < @asof
    AND email IS NOT NULL
  GROUP BY 1,2
), sales AS (
  SELECT order_name, ROUND(SUM(net_sales_usd), 2) net_sales
  FROM `hd-global-analytics-platform.mart_ecom_tracker.net_sales_bookings`
  WHERE country = 'US' AND data_source = 'Shopify'
    AND date_ref >= '2025-06-23' AND date_ref < @asof
  GROUP BY 1 HAVING net_sales > 0
), purchases AS (
  SELECT o.email, o.order_name, o.placed_at, s.net_sales
  FROM orders o JOIN sales s USING (order_name)
), matched AS (
  SELECT c.*,
    COUNTIF(p.placed_at > c.first_contact AND p.placed_at <= TIMESTAMP_ADD(c.first_contact, INTERVAL 3 DAY)) orders_3d,
    COUNTIF(p.placed_at > c.first_contact AND p.placed_at <= TIMESTAMP_ADD(c.first_contact, INTERVAL 30 DAY)) orders_30d,
    COUNTIF(p.placed_at <= (SELECT data_through FROM freshness)) orders_to_date,
    SUM(IF(p.placed_at <= (SELECT data_through FROM freshness), p.net_sales, 0)) revenue_to_date,
    SUM(IF(p.placed_at > c.first_contact AND p.placed_at <= TIMESTAMP_ADD(c.first_contact, INTERVAL 3 DAY), p.net_sales, 0)) revenue_3d,
    SUM(IF(p.placed_at > c.first_contact AND p.placed_at <= TIMESTAMP_ADD(c.first_contact, INTERVAL 30 DAY), p.net_sales, 0)) revenue_30d
  FROM contacts c LEFT JOIN purchases p ON c.email = p.email
    AND p.placed_at > c.first_contact AND p.placed_at <= TIMESTAMP_ADD(c.first_contact, INTERVAL 30 DAY)
  GROUP BY c.grain,c.period,c.mailbox,c.email,c.first_contact,c.tickets,c.replies,c.replied_tickets,c.reply_minutes
)
SELECT grain, period, mailbox, COUNT(*) contacts, SUM(tickets) tickets,
  COUNTIF(replied_tickets > 0) replied_contacts, SUM(replied_tickets) replied_tickets,
  SUM(replies) agent_replies,
  APPROX_QUANTILES(IF(replied_tickets > 0, reply_minutes, NULL), 100)[OFFSET(50)] median_reply_business_min,
  COUNTIF(DATE(first_contact, 'America/Phoenix') < DATE(TIMESTAMP_SUB(f.data_through, INTERVAL 3 DAY), 'America/Phoenix')) mature_3d,
  COUNTIF(DATE(first_contact, 'America/Phoenix') < DATE(TIMESTAMP_SUB(f.data_through, INTERVAL 3 DAY), 'America/Phoenix') AND orders_3d > 0) buyers_3d,
  ROUND(SUM(IF(DATE(first_contact, 'America/Phoenix') < DATE(TIMESTAMP_SUB(f.data_through, INTERVAL 3 DAY), 'America/Phoenix'), revenue_3d, 0)), 2) revenue_3d,
  COUNTIF(DATE(first_contact, 'America/Phoenix') < DATE(TIMESTAMP_SUB(f.data_through, INTERVAL 30 DAY), 'America/Phoenix')) mature_30d,
  COUNTIF(DATE(first_contact, 'America/Phoenix') < DATE(TIMESTAMP_SUB(f.data_through, INTERVAL 30 DAY), 'America/Phoenix') AND orders_30d > 0) buyers_30d,
  ROUND(SUM(IF(DATE(first_contact, 'America/Phoenix') < DATE(TIMESTAMP_SUB(f.data_through, INTERVAL 30 DAY), 'America/Phoenix'), revenue_30d, 0)), 2) revenue_30d,
  COUNTIF(orders_to_date > 0) buyers_to_date, SUM(orders_to_date) orders_to_date,
  ROUND(SUM(revenue_to_date), 2) revenue_to_date,
  CAST(f.data_through AS STRING) data_through
FROM matched CROSS JOIN freshness f
GROUP BY 1,2,3,f.data_through
ORDER BY grain,period,mailbox
"""


def collect(client, asof: date) -> dict:
    from google.cloud import bigquery

    config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("asof", "DATE", asof)
    ])
    rows = [{key: float(value) if isinstance(value, Decimal) else value
             for key, value in row.items()}
            for row in client.query(SQL, job_config=config).result()]
    return {"asof": asof.isoformat(), "rows": rows}
