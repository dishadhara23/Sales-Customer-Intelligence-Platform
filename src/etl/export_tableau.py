"""Chart-ready extracts for the generated Tableau workbook.

These differ from ``export_extracts.py`` on purpose. Those extracts are general
purpose — full grain, several thousand rows, meant for someone building sheets
by hand who may want to slice them.

These are the opposite: each file is already exactly the table one chart needs,
with every filter applied and every weighted average resolved in SQL. That
matters because the workbook is generated rather than clicked together, and
every calculated field or filter it would otherwise need is another piece of
Tableau XML that could be subtly wrong. Pushing that work into SQL — where it is
tested — means the generated sheets are a plain drag of two or three fields.

It also preserves the architectural point: business logic lives in SQL, and the
BI tool only draws.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.config import EXTRACT_DIR, Warehouse

log = logging.getLogger(__name__)

# name -> (SQL, one-line description used in the workbook caption)
SHEETS: dict[str, tuple[str, str]] = {
    "tab_kpi": ("""
        SELECT
            ROUND(revenue)                         AS revenue,
            valid_orders                           AS orders,
            customers,
            ROUND(revenue / valid_orders, 2)       AS avg_order_value,
            units,
            avg_review_score,
            avg_days_to_deliver,
            late_delivery_pct
        FROM mart_dash_kpi
        WHERE region = 'ALL' AND year_label = 'ALL'
    """, "Headline KPIs"),

    "tab_trend": ("""
        SELECT year_month, ROUND(revenue) AS revenue, valid_orders AS orders,
               ROUND(avg_order_value) AS avg_order_value,
               new_customers, avg_review_score
        FROM mart_kpi_monthly
        WHERE valid_orders >= 50
        ORDER BY month_start_date
    """, "Monthly revenue trend"),

    # The weighted review score is resolved here. A plain AVG in Tableau over
    # the month x region x bucket grain would weight a 12-order row the same as
    # a 4,000-order one and quietly report the wrong number.
    "tab_delivery": ("""
        SELECT
            delivery_speed_bucket                                   AS bucket,
            bucket_order,
            SUM(orders)                                             AS orders,
            ROUND(SUM(orders * avg_review_score) / SUM(orders), 2)   AS avg_review_score,
            ROUND(100.0 * SUM(detractor_orders) / SUM(orders), 1)    AS detractor_pct,
            ROUND(SUM(revenue))                                      AS revenue
        FROM mart_delivery_performance
        WHERE avg_review_score IS NOT NULL
        GROUP BY delivery_speed_bucket, bucket_order
        ORDER BY bucket_order
    """, "Review score by delivery speed"),

    "tab_cohort": ("""
        SELECT cohort_label, months_since_first_order AS month_index,
               retention_pct, cohort_customers, active_customers
        FROM mart_cohort_retention
        WHERE cohort_customers >= 500 AND months_since_first_order <= 11
        ORDER BY cohort_label, months_since_first_order
    """, "Cohort retention grid"),

    "tab_category": ("""
        SELECT category, ROUND(gross_revenue) AS revenue,
               pct_of_total_revenue AS pct_of_revenue,
               cumulative_revenue_pct AS cumulative_pct,
               freight_pct_of_revenue AS freight_pct,
               avg_review_score, units_sold AS units
        FROM mart_category_performance
        WHERE revenue_rank <= 15
        ORDER BY revenue_rank
    """, "Category revenue concentration"),

    "tab_geo": ("""
        SELECT state_code, region, ROUND(revenue) AS revenue,
               pct_of_national_revenue AS pct_of_revenue,
               avg_order_value, avg_days_to_deliver, avg_review_score,
               late_delivery_rate_pct, customers, latitude, longitude
        FROM mart_geo_performance
        WHERE latitude IS NOT NULL
        ORDER BY revenue_rank
    """, "State performance and location"),

    "tab_funnel": ("""
        SELECT stage, stage_order, SUM(orders) AS orders,
               ROUND(100.0 * SUM(orders)
                     / (SELECT SUM(orders) FROM mart_order_funnel WHERE stage_order = 1), 1)
                   AS pct_of_purchased
        FROM mart_order_funnel
        GROUP BY stage, stage_order
        ORDER BY stage_order
    """, "Fulfilment funnel"),

    # region='ALL' only: the mart holds per-region rows *and* the national
    # rollup, so an unfiltered sheet would double count every customer.
    "tab_rfm": ("""
        SELECT segment, customers, ROUND(revenue) AS revenue,
               avg_value, avg_orders, avg_recency_days
        FROM mart_dash_rfm
        WHERE region = 'ALL'
        ORDER BY revenue DESC
    """, "RFM segments"),
}


def export_tableau_sheets(wh: Warehouse, out_dir: Path | None = None) -> list[Path]:
    out_dir = out_dir or EXTRACT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, (sql, _desc) in SHEETS.items():
        df = pd.read_sql(sql, wh.engine)
        path = out_dir / f"{name}.csv"
        df.to_csv(path, index=False)
        written.append(path)
        log.info("  tableau sheet %-14s %5s rows x %2s cols -> %s",
                 name, len(df), df.shape[1], path.name)
    return written
