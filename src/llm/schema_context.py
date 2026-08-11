"""Build the schema briefing that the text-to-SQL agent is grounded on.

Two decisions matter here:

* **Only the curated layer is exposed.** ``stg_*`` tables are hidden. The
  staging tables are where all the traps live — the per-order ``customer_id``
  that breaks retention, the un-collapsed geolocation table that fans joins out
  30x. Hiding them means the model cannot reach for them, so the modelling
  decisions made in SQL are enforced rather than merely recommended.

* **Low-cardinality columns ship their actual values.** Without them the model
  guesses at literals — ``WHERE category = 'Health & Beauty'`` returns zero
  rows against a column that actually holds ``health_beauty``. Enumerating the
  values costs a few hundred tokens once and removes an entire class of
  silently-empty results.

The briefing is deterministic for a given warehouse, so it sits at the front of
the prompt behind a cache breakpoint and is billed at cache-read rates after
the first call.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy import inspect, text

from src.config import Warehouse

# Tables the agent may query, in the order they should be presented.
EXPOSED_TABLES: tuple[str, ...] = (
    "fact_orders",
    "fact_order_items",
    "fact_payments",
    "dim_customer",
    "dim_product",
    "dim_seller",
    "dim_date",
    "dim_geography",
    "mart_kpi_daily",
    "mart_kpi_monthly",
    "mart_rfm",
    "mart_customer_360",
    "mart_cohort_retention",
    "mart_order_funnel",
    "mart_category_performance",
    "mart_geo_performance",
    "mart_delivery_performance",
    "mart_payment_mix",
)

TABLE_NOTES: dict[str, str] = {
    "fact_orders": "One row per order. THE default table for revenue/order questions. "
                   "Filter is_valid_sale = 1 to exclude cancelled/unavailable orders.",
    "fact_order_items": "One row per line item (order_id + order_item_id). Use for "
                        "product/category/seller questions. item_price is per unit.",
    "fact_payments": "One row per payment instrument on an order. Never join to "
                     "fact_orders and sum revenue — it double counts.",
    "dim_customer": "One row per PERSON (customer_key = customer_unique_id). "
                    "Never use the raw per-order customer_id for customer counts.",
    "mart_kpi_daily": "Pre-aggregated daily KPIs on a complete date spine.",
    "mart_kpi_monthly": "Pre-aggregated monthly KPIs incl. MoM growth and new-vs-returning split.",
    "mart_rfm": "Recency/Frequency/Monetary scores + rfm_segment, one row per customer.",
    "mart_customer_360": "Wide customer view: dim_customer joined to mart_rfm. Start here "
                         "for 'which customers...' questions.",
    "mart_cohort_retention": "Acquisition cohort x months_since_first_order retention grid.",
    "mart_order_funnel": "Fulfilment funnel by month, built on milestone timestamps.",
    "mart_category_performance": "Category revenue, freight burden, Pareto share, review score.",
    "mart_geo_performance": "State-level revenue, AOV, delivery and review metrics + lat/lng.",
    "mart_delivery_performance": "Delivery-speed bucket x month x region vs review outcome.",
    "mart_payment_mix": "Payment instrument and instalment mix by month.",
}

# Columns whose names invite the wrong choice. A model that picks
# delivery_vs_estimate_days for "delivery time" gets negative numbers and no
# error — the query succeeds, so nothing catches it. Naming the trap in the
# briefing is cheaper than trying to detect the mistake afterwards.
COLUMN_NOTES: dict[str, str] = {
    "days_to_deliver":
        "purchase -> customer delivery, in whole days. THIS is 'delivery time'.",
    "delivery_vs_estimate_days":
        "actual minus ESTIMATED delivery. Negative = arrived early. "
        "NOT delivery time — do not use it to answer 'how long did delivery take'.",
    "is_late_delivery":
        "1 if delivered after the estimate, 0 if on time, NULL if never delivered.",
    "is_valid_sale":
        "1 for orders that count as a sale. Filter on this for revenue.",
    "gross_revenue": "item revenue + freight. The default revenue measure.",
    "item_revenue": "goods only, excluding freight.",
    "customer_key":
        "the PERSON. Use for customer counts and retention.",
    "source_customer_id":
        "Olist's per-ORDER id. Never count customers with this — it makes every "
        "buyer look like a first-time buyer.",
    "review_score": "1-5 stars, NULL if unreviewed. Not a monetary value.",
    "cohort_month": "month of the customer's first order.",
    "months_since_first_order": "0 = the acquisition month itself.",
    "retention_pct": "already a percentage, 0-100. Do not multiply by 100 again.",
    "bucket_order": "sort key for delivery bands. Order by this, not alphabetically.",
    "stage_order": "sort key for funnel stages.",
}

MAX_ENUM_VALUES = 30
ENUM_CANDIDATE_COLUMNS = {
    "order_status", "payment_type", "instalment_band", "region", "state_code",
    "rfm_segment", "value_tier", "delivery_speed_bucket", "stage", "weight_band",
    "month_name", "day_name",
}


@dataclass(frozen=True)
class SchemaBriefing:
    text: str
    tables: tuple[str, ...]

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.text


_PANDAS_TO_SQL_TYPE = {
    "int64": "integer", "Int64": "integer", "int32": "integer",
    "float64": "numeric", "float32": "numeric",
    "bool": "boolean",
    "datetime64[ns]": "timestamp",
    "object": "text", "string": "text",
}


def _column_lines(wh: Warehouse, table: str) -> list[str]:
    """Column list with a usable type for every column.

    SQLite's ``CREATE TABLE ... AS SELECT`` leaves columns untyped, so
    reflection reports NULL for most of the warehouse. An untyped briefing
    makes the model hedge (casting everything, quoting numbers), so where
    reflection is uninformative the type is inferred from a sample row instead.
    """
    insp = inspect(wh.engine)
    columns = insp.get_columns(table)

    sample: pd.DataFrame | None = None
    if any(str(c["type"]).lower() in {"null", "none", ""} for c in columns):
        sample = pd.read_sql(f"SELECT * FROM {table} LIMIT 200", wh.engine)

    lines = []
    for col in columns:
        type_name = str(col["type"]).split("(")[0].lower()
        if type_name in {"null", "none", ""} and sample is not None and col["name"] in sample:
            dtype = str(sample[col["name"]].dtype)
            type_name = _PANDAS_TO_SQL_TYPE.get(dtype, dtype)
        note = COLUMN_NOTES.get(col["name"])
        suffix = f"  -- {note}" if note else ""
        lines.append(f"    {col['name']} ({type_name}){suffix}")
    return lines


def _enum_values(wh: Warehouse, table: str, column: str) -> list[str] | None:
    with wh.engine.connect() as conn:
        distinct = conn.execute(
            text(f"SELECT COUNT(DISTINCT {column}) FROM {table}")
        ).scalar_one()
        if distinct is None or distinct > MAX_ENUM_VALUES:
            return None
        rows = conn.execute(
            text(f"SELECT DISTINCT {column} FROM {table} WHERE {column} IS NOT NULL "
                 f"ORDER BY {column}")
        ).fetchall()
    return [str(r[0]) for r in rows]


def build_briefing(wh: Warehouse) -> SchemaBriefing:
    insp = inspect(wh.engine)
    existing = set(insp.get_table_names()) | set(insp.get_view_names())
    tables = tuple(t for t in EXPOSED_TABLES if t in existing)

    parts: list[str] = [
        f"DIALECT: {wh.dialect}",
        "",
        "You may query ONLY the tables below. Staging (stg_*) tables are out of scope.",
        "",
    ]

    with wh.engine.connect() as conn:
        for table in tables:
            row_count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            parts.append(f"TABLE {table}  ({row_count:,} rows)")
            if note := TABLE_NOTES.get(table):
                parts.append(f"  -- {note}")
            parts.extend(_column_lines(wh, table))
            parts.append("")

    # Enumerate the small categorical columns once, globally.
    parts.append("CATEGORICAL COLUMN VALUES (use these literals exactly):")
    seen: set[str] = set()
    for table in tables:
        cols = {c["name"] for c in insp.get_columns(table)}
        for column in sorted(cols & ENUM_CANDIDATE_COLUMNS):
            if column in seen:
                continue
            values = _enum_values(wh, table, column)
            if values:
                seen.add(column)
                parts.append(f"  {column}: {', '.join(values)}")
    parts.append("")

    return SchemaBriefing(text="\n".join(parts), tables=tables)


_BRIEFING_CACHE: dict[str, SchemaBriefing] = {}


def get_briefing(wh: Warehouse) -> SchemaBriefing:
    """Build once per warehouse URL and reuse.

    Beyond saving the introspection round-trips, this keeps the briefing
    byte-identical between questions, which is what lets the Anthropic prompt
    cache hit on the system block instead of re-billing ~2.6k tokens per turn.
    """
    if wh.url not in _BRIEFING_CACHE:
        _BRIEFING_CACHE[wh.url] = build_briefing(wh)
    return _BRIEFING_CACHE[wh.url]


def preview_table(wh: Warehouse, table: str, limit: int = 5) -> pd.DataFrame:
    return pd.read_sql(f"SELECT * FROM {table} LIMIT {limit}", wh.engine)
