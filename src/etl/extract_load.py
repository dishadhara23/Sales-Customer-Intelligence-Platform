"""Extract the raw Olist CSVs and load them into the warehouse as staging tables.

Design notes
------------
* Staging is deliberately *thin*: types are fixed, whitespace and casing are
  normalised, exact duplicates are dropped — and nothing else. All business
  logic lives in the SQL models so it is reviewable as SQL.
* ``geolocation`` is the one exception. It ships ~1M rows (multiple lat/lng
  readings per postcode) which is a join hazard downstream, so it is collapsed
  to one row per postcode prefix here, in pandas, where a median is cheap.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from src.config import RAW_DIR, Warehouse
from src.etl.sql_runner import drop_relation

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Source:
    table: str
    filename: str
    date_columns: tuple[str, ...] = ()
    text_columns: tuple[str, ...] = ()
    dedupe_on: tuple[str, ...] | None = None


SOURCES: tuple[Source, ...] = (
    Source(
        table="stg_customers",
        filename="olist_customers_dataset.csv",
        text_columns=("customer_city", "customer_state"),
        dedupe_on=("customer_id",),
    ),
    Source(
        table="stg_orders",
        filename="olist_orders_dataset.csv",
        date_columns=(
            "order_purchase_timestamp",
            "order_approved_at",
            "order_delivered_carrier_date",
            "order_delivered_customer_date",
            "order_estimated_delivery_date",
        ),
        text_columns=("order_status",),
        dedupe_on=("order_id",),
    ),
    Source(
        table="stg_order_items",
        filename="olist_order_items_dataset.csv",
        date_columns=("shipping_limit_date",),
        dedupe_on=("order_id", "order_item_id"),
    ),
    Source(
        table="stg_order_payments",
        filename="olist_order_payments_dataset.csv",
        text_columns=("payment_type",),
        dedupe_on=("order_id", "payment_sequential"),
    ),
    Source(
        table="stg_order_reviews",
        filename="olist_order_reviews_dataset.csv",
        date_columns=("review_creation_date", "review_answer_timestamp"),
        dedupe_on=("review_id", "order_id"),
    ),
    Source(
        table="stg_products",
        filename="olist_products_dataset.csv",
        text_columns=("product_category_name",),
        dedupe_on=("product_id",),
    ),
    Source(
        table="stg_sellers",
        filename="olist_sellers_dataset.csv",
        text_columns=("seller_city", "seller_state"),
        dedupe_on=("seller_id",),
    ),
    Source(
        table="stg_category_translation",
        filename="product_category_name_translation.csv",
        text_columns=("product_category_name", "product_category_name_english"),
        dedupe_on=("product_category_name",),
    ),
)

GEO_SOURCE = Source(
    table="stg_geolocation",
    filename="olist_geolocation_dataset.csv",
    text_columns=("geolocation_city", "geolocation_state"),
)


def _clean(df: pd.DataFrame, src: Source) -> pd.DataFrame:
    for col in src.text_columns:
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip().str.lower()

    for col in src.date_columns:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    before = len(df)
    if src.dedupe_on:
        df = df.drop_duplicates(subset=list(src.dedupe_on), keep="first")
    else:
        df = df.drop_duplicates()
    if (dropped := before - len(df)):
        log.info("  %s: dropped %s duplicate row(s)", src.table, f"{dropped:,}")

    return df.reset_index(drop=True)


def _read(src: Source) -> pd.DataFrame:
    path = RAW_DIR / src.filename
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Run `python scripts/download_data.py` first."
        )
    # utf-8-sig strips the BOM that ships on the translation file.
    return pd.read_csv(path, encoding="utf-8-sig")


def _collapse_geolocation(df: pd.DataFrame) -> pd.DataFrame:
    """One row per postcode prefix: median coordinate, most common place name.

    The raw table has one row per *delivery event*, so a naive join fans orders
    out by ~30x. Median (not mean) because a handful of rows carry coordinates
    outside Brazil entirely and would drag a mean across the map.
    """
    df = df[
        df["geolocation_lat"].between(-34.0, 5.3) & df["geolocation_lng"].between(-74.0, -34.7)
    ]

    def _mode(s: pd.Series) -> str | None:
        m = s.mode()
        return m.iloc[0] if len(m) else None

    out = (
        df.groupby("geolocation_zip_code_prefix", as_index=False)
        .agg(
            geolocation_lat=("geolocation_lat", "median"),
            geolocation_lng=("geolocation_lng", "median"),
            geolocation_city=("geolocation_city", _mode),
            geolocation_state=("geolocation_state", _mode),
            geolocation_points=("geolocation_lat", "size"),
        )
    )
    return out


def _write(wh: Warehouse, table: str, df: pd.DataFrame) -> int:
    with wh.engine.begin() as conn:
        drop_relation(conn, wh.dialect, table)
    df.to_sql(table, wh.engine, index=False, if_exists="replace", chunksize=10_000, method="multi")
    return len(df)


def load_staging(wh: Warehouse) -> dict[str, int]:
    """Load every source CSV into its staging table. Returns row counts."""
    counts: dict[str, int] = {}

    for src in SOURCES:
        df = _clean(_read(src), src)
        counts[src.table] = _write(wh, src.table, df)
        log.info("loaded %-26s %9s rows", src.table, f"{counts[src.table]:,}")

    geo = _clean(_read(GEO_SOURCE), GEO_SOURCE)
    geo = _collapse_geolocation(geo)
    counts[GEO_SOURCE.table] = _write(wh, GEO_SOURCE.table, geo)
    log.info("loaded %-26s %9s rows (collapsed from raw delivery events)",
             GEO_SOURCE.table, f"{counts[GEO_SOURCE.table]:,}")

    return counts


def raw_files_present() -> list[Path]:
    wanted = [s.filename for s in SOURCES] + [GEO_SOURCE.filename]
    return [RAW_DIR / f for f in wanted if not (RAW_DIR / f).exists()]
