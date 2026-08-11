"""Data-quality assertions that run after every build.

Each check is a named SQL query returning a single number plus a predicate the
number must satisfy. Keeping them declarative means a reviewer can read the
contract for the warehouse without reading the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import text

from src.config import Warehouse


@dataclass
class Check:
    name: str
    sql: str
    predicate: Callable[[float], bool]
    expectation: str


@dataclass
class CheckOutcome:
    name: str
    value: float
    expectation: str
    passed: bool


@dataclass
class CheckReport:
    outcomes: list[CheckOutcome] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if not o.passed)

    def render(self) -> str:
        width = max((len(o.name) for o in self.outcomes), default=20)
        lines = [f"{'check'.ljust(width)}  {'value':>14}  expectation"]
        lines.append("-" * (width + 40))
        for o in self.outcomes:
            mark = "PASS" if o.passed else "FAIL"
            value = f"{o.value:,.2f}" if o.value % 1 else f"{int(o.value):,}"
            lines.append(f"{o.name.ljust(width)}  {value:>14}  {o.expectation}  [{mark}]")
        lines.append("")
        lines.append(f"{len(self.outcomes) - self.failed}/{len(self.outcomes)} checks passed.")
        return "\n".join(lines)


CHECKS: tuple[Check, ...] = (
    Check(
        "orders_row_count",
        "SELECT COUNT(*) FROM fact_orders",
        lambda v: v == 99_441,
        "== 99,441 (published Olist order count)",
    ),
    Check(
        "orders_unique_grain",
        "SELECT COUNT(*) - COUNT(DISTINCT order_id) FROM fact_orders",
        lambda v: v == 0,
        "== 0 duplicate order_id",
    ),
    Check(
        "order_items_unique_grain",
        "SELECT COUNT(*) - COUNT(DISTINCT order_item_key) FROM fact_order_items",
        lambda v: v == 0,
        "== 0 duplicate line-item key",
    ),
    Check(
        "customers_collapse_ratio",
        "SELECT COUNT(*) FROM dim_customer",
        lambda v: 90_000 < v < 99_441,
        "between 90k and 99,441 (unique people < order-level ids)",
    ),
    Check(
        "no_orphan_orders",
        "SELECT COUNT(*) FROM fact_orders WHERE customer_key IS NULL",
        lambda v: v == 0,
        "== 0 orders without a customer",
    ),
    Check(
        "revenue_reconciles_to_line_items",
        """
        SELECT ABS(
            (SELECT SUM(gross_revenue)   FROM fact_orders)
          - (SELECT SUM(item_gross_value) FROM fact_order_items)
        )
        """,
        lambda v: v < 1.0,
        "< R$1.00 difference between order and line-item revenue",
    ),
    Check(
        "no_negative_revenue",
        "SELECT COUNT(*) FROM fact_order_items WHERE item_price < 0 OR freight_value < 0",
        lambda v: v == 0,
        "== 0 negative price or freight",
    ),
    Check(
        "date_spine_is_contiguous",
        "SELECT COUNT(*) - COUNT(DISTINCT calendar_date) FROM dim_date",
        lambda v: v == 0,
        "== 0 duplicate calendar dates",
    ),
    Check(
        "every_order_date_in_dim_date",
        """
        SELECT COUNT(*) FROM fact_orders f
        LEFT JOIN dim_date d ON d.date_key = f.date_key
        WHERE d.date_key IS NULL
        """,
        lambda v: v == 0,
        "== 0 fact rows missing a date dimension row",
    ),
    Check(
        "review_scores_in_range",
        "SELECT COUNT(*) FROM fact_orders WHERE review_score IS NOT NULL "
        "AND (review_score < 1 OR review_score > 5)",
        lambda v: v == 0,
        "== 0 review scores outside 1-5",
    ),
    Check(
        "rfm_covers_all_purchasers",
        """
        SELECT (SELECT COUNT(*) FROM mart_rfm)
             - (SELECT COUNT(DISTINCT customer_key) FROM fact_orders WHERE is_valid_sale = 1)
        """,
        lambda v: v == 0,
        "== 0 (every purchasing customer is scored)",
    ),
    Check(
        "cohort_retention_month0_is_100pct",
        "SELECT MIN(retention_pct) FROM mart_cohort_retention WHERE months_since_first_order = 0",
        lambda v: abs(v - 100.0) < 0.001,
        "== 100% at month 0 by definition",
    ),
)


def run_checks(wh: Warehouse) -> CheckReport:
    report = CheckReport()
    with wh.engine.connect() as conn:
        for check in CHECKS:
            raw = conn.execute(text(check.sql)).scalar()
            value = float(raw) if raw is not None else float("nan")
            report.outcomes.append(
                CheckOutcome(check.name, value, check.expectation, bool(check.predicate(value)))
            )
    return report
