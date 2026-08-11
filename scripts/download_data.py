#!/usr/bin/env python3
"""Fetch the Olist Brazilian E-Commerce dataset into ``data/raw/``.

The canonical home of this dataset is Kaggle, which requires an account and an
API token. To keep the project clone-and-run, this script pulls the same nine
CSVs from a public mirror. Row counts are asserted against the published
figures so a silently truncated or substituted mirror fails loudly rather than
producing quietly wrong analytics.

    python scripts/download_data.py
"""

from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parents[1] / "data" / "raw"

MIRROR = (
    "https://raw.githubusercontent.com/"
    "Judithokon/olist-ecommerce-sales-data-analysis-using-python/main"
)

# filename -> expected data row count (excluding header)
FILES: dict[str, int] = {
    "olist_customers_dataset.csv": 99441,
    "olist_geolocation_dataset.csv": 1000163,
    "olist_order_items_dataset.csv": 112650,
    "olist_order_payments_dataset.csv": 103886,
    # 100,000 rows but only 99,173 distinct review_id: a single review can be
    # attached to more than one order. Deduplicate on (review_id, order_id).
    "olist_order_reviews_dataset.csv": 100000,
    "olist_orders_dataset.csv": 99441,
    "olist_products_dataset.csv": 32951,
    "olist_sellers_dataset.csv": 3095,
    "product_category_name_translation.csv": 71,
}


def count_rows(path: Path) -> int:
    """Count CSV data rows, tolerating embedded newlines in quoted fields."""
    import csv

    with path.open(newline="", encoding="utf-8-sig") as fh:
        return sum(1 for _ in csv.reader(fh)) - 1


def main() -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    for name, expected in FILES.items():
        dest = RAW_DIR / name
        if dest.exists() and count_rows(dest) == expected:
            print(f"  ok (cached)  {name}")
            continue

        url = f"{MIRROR}/{name}"
        print(f"  downloading  {name} ...", end="", flush=True)
        try:
            urllib.request.urlretrieve(url, dest)
        except Exception as exc:
            print(f" FAILED ({exc})")
            failures.append(name)
            continue

        actual = count_rows(dest)
        if actual != expected:
            print(f" ROW COUNT MISMATCH: got {actual:,}, expected {expected:,}")
            failures.append(name)
        else:
            print(f" {actual:,} rows")

    if failures:
        print(f"\n{len(failures)} file(s) failed: {', '.join(failures)}", file=sys.stderr)
        print(
            "Fall back to Kaggle: "
            "https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce",
            file=sys.stderr,
        )
        return 1

    print(f"\nAll {len(FILES)} files present in {RAW_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
