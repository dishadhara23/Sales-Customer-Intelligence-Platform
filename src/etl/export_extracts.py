"""Write flat CSV extracts for the Tableau workbook.

Tableau connects to these files rather than to the warehouse directly so the
published dashboard works on Tableau Public (which cannot reach a local
database) and so a reviewer can open the workbook without any credentials.

The extract set is deliberately small and pre-aggregated: the marts already did
the analytics, so Tableau is doing presentation only.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from src.config import EXTRACT_DIR, Warehouse

log = logging.getLogger(__name__)

# table -> optional ORDER BY, keeping the CSVs diff-friendly across rebuilds
EXTRACTS: dict[str, str] = {
    "mart_kpi_daily": "date_key",
    "mart_kpi_monthly": "month_start_date",
    # Segment-level, not customer-level: the RFM sheet only needs the summary,
    # and a 95k-row extract would be 13 MB of repo for no extra insight.
    "mart_dash_rfm": "region, segment",
    "mart_cohort_retention": "cohort_month, months_since_first_order",
    "mart_dash_kpi": "region, year_label",
    "mart_order_funnel": "month_start_date, stage_order",
    "mart_category_performance": "revenue_rank",
    "mart_geo_performance": "revenue_rank",
    "mart_delivery_performance": "month_start_date, region, bucket_order",
    "mart_payment_mix": "month_start_date, payment_type, instalment_band",
}


def export_all(wh: Warehouse, out_dir: Path | None = None) -> list[Path]:
    out_dir = out_dir or EXTRACT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for table, order_by in EXTRACTS.items():
        sql = f"SELECT * FROM {table}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        df = pd.read_sql(sql, wh.engine)
        path = out_dir / f"{table}.csv"
        df.to_csv(path, index=False)
        written.append(path)
        log.info("  extract %-28s %8s rows -> %s", table, f"{len(df):,}", path.name)

    # A tiny summary the README and the dashboard subtitle can both quote.
    summary = pd.read_sql(
        """
        SELECT
            (SELECT COUNT(*) FROM fact_orders)                       AS orders,
            (SELECT COUNT(*) FROM dim_customer)                      AS customers,
            (SELECT COUNT(*) FROM fact_order_items)                  AS line_items,
            (SELECT ROUND(SUM(gross_revenue)) FROM fact_orders
              WHERE is_valid_sale = 1)                               AS gross_revenue,
            (SELECT MIN(order_date) FROM fact_orders)                AS first_order_date,
            (SELECT MAX(order_date) FROM fact_orders)                AS last_order_date
        """,
        wh.engine,
    )
    path = out_dir / "_dataset_summary.csv"
    summary.to_csv(path, index=False)
    written.append(path)

    return written
