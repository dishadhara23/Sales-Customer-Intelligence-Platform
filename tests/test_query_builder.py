"""The no-model query builder: parsing, SQL generation, and honest failure.

This is the backend that runs on a fresh clone with nothing installed, so it is
the one a reviewer is most likely to meet. It is deterministic, which means it
is also fully testable — every case below is an assertion about meaning, not
about phrasing.

The parsing tests exist because of real bugs. "Revenue in the south" was read as
*break down by region* rather than *filter to South*, and "worst review score"
sorted descending because the code assumed high always meant good.
"""

from __future__ import annotations

import pytest

from src.llm import query_builder as qb
from src.llm.sql_guard import guard


# --- what the question means ----------------------------------------------

@pytest.mark.parametrize("question, metric", [
    ("total revenue", "revenue"),
    ("how many orders were there", "orders"),
    ("how many customers", "customers"),
    ("average order value by state", "aov"),
    ("average review score by category", "review"),
    ("average delivery time by region", "delivery_days"),
    ("late delivery rate by state", "late_pct"),
    ("freight cost by category", "freight"),
    ("cancellation rate by state", "cancel_pct"),
])
def test_metric_is_identified(question, metric):
    plan = qb.parse(question)
    assert plan is not None, f"failed to parse: {question}"
    assert plan.metric.key == metric


@pytest.mark.parametrize("question, dimension", [
    ("revenue by region", "region"),
    ("revenue by state", "state"),
    ("revenue by city", "city"),
    ("monthly revenue", "month"),
    ("revenue by payment type", "payment"),
    ("revenue by category", "category"),
    ("orders by weekday", "weekday"),
])
def test_dimension_is_identified(question, dimension):
    plan = qb.parse(question)
    assert plan is not None, f"failed to parse: {question}"
    assert plan.dimension is not None
    assert plan.dimension.key == dimension


def test_a_bare_metric_has_no_breakdown():
    plan = qb.parse("total revenue in 2018")
    assert plan.dimension is None


# --- filters, which used to be mistaken for breakdowns ---------------------

def test_region_filter_is_not_read_as_a_breakdown():
    """"in the south" filters to South. It is not a request to group by region."""
    plan = qb.parse("revenue in the south")
    assert plan.region == "South"
    assert plan.dimension is None


def test_a_filter_and_a_breakdown_can_coexist():
    plan = qb.parse("revenue by state in the northeast")
    assert plan.region == "Northeast"
    assert plan.dimension.key == "state"


def test_year_is_extracted_as_a_filter():
    plan = qb.parse("revenue by category in 2017")
    assert plan.year == 2017
    assert plan.dimension.key == "category"


def test_a_year_outside_the_data_is_not_treated_as_a_year():
    plan = qb.parse("revenue in 2024")
    assert plan is None or plan.year is None


# --- sort direction, where "worst" depends on the measure ------------------

def test_worst_review_score_sorts_ascending():
    """High review scores are good, so the worst are the lowest."""
    plan = qb.parse("which categories have the worst review scores")
    assert plan.ascending is True


def test_worst_delivery_time_sorts_descending():
    """Low delivery times are good, so the worst are the highest."""
    plan = qb.parse("which states have the worst delivery times")
    assert plan.ascending is False


def test_slowest_delivery_sorts_descending():
    plan = qb.parse("which states have the slowest delivery")
    assert plan.ascending is False


def test_best_review_score_sorts_descending():
    plan = qb.parse("categories with the best review scores")
    assert plan.ascending is False


def test_default_sort_puts_the_largest_first():
    plan = qb.parse("revenue by state")
    assert plan.ascending is False


def test_time_series_is_ordered_by_period_not_by_size():
    """A monthly trend read best-first would not be a trend."""
    plan = qb.parse("monthly revenue for 2018")
    assert plan.dimension.order_by_self


# --- top N ------------------------------------------------------------------

def test_top_n_is_honoured():
    plan = qb.parse("top 5 states by revenue")
    assert plan.limit == 5


# --- generated SQL ----------------------------------------------------------

@pytest.mark.parametrize("question", qb.SUGGESTIONS)
def test_every_suggestion_produces_guard_safe_sql(question):
    """The UI offers these, so none may be rejected by the security guard."""
    plan = qb.parse(question)
    assert plan is not None, f"builder cannot parse its own suggestion: {question}"
    sql = qb.to_sql(plan, row_limit=1000)
    guard(sql, {t for t in _TABLES}, 1000)          # raises if unsafe


_TABLES = {
    "fact_orders", "fact_order_items", "fact_payments", "fact_reviews",
    "dim_customer", "dim_product", "dim_seller", "dim_date", "dim_geography",
    "mart_kpi_daily", "mart_kpi_monthly", "mart_rfm", "mart_customer_360",
    "mart_cohort_retention", "mart_order_funnel", "mart_category_performance",
    "mart_geo_performance", "mart_delivery_performance", "mart_payment_mix",
}


def test_generated_sql_never_writes():
    for question in qb.SUGGESTIONS:
        sql = qb.to_sql(qb.parse(question), row_limit=100).lower()
        assert not any(w in sql.split() for w in
                       ("insert", "update", "delete", "drop", "create", "alter"))


# --- failing honestly -------------------------------------------------------

def test_nonsense_returns_no_plan_rather_than_a_guess():
    """Answering the wrong question confidently is worse than declining."""
    assert qb.parse("what is the meaning of life") is None
    assert qb.parse("hello") is None


def test_failure_message_names_what_it_does_understand():
    message = qb.explain_failure("what is the meaning of life")
    assert "revenue" in message.lower()
    assert "not a language model" in message.lower()
