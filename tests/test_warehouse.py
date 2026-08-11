"""Integration tests against a built warehouse.

These run the same assertions the pipeline runs, plus the business-logic
invariants that are easy to break silently — grain, fan-out, and the customer
key. They skip (rather than fail) if the warehouse has not been built, so a
fresh clone can run the unit tests without a database.
"""

from __future__ import annotations

import pandas as pd
import pytest
from sqlalchemy import inspect

from src.config import get_warehouse
from src.etl.checks import run_checks


@pytest.fixture(scope="module")
def wh():
    warehouse = get_warehouse()
    tables = set(inspect(warehouse.engine).get_table_names())
    if "fact_orders" not in tables:
        pytest.skip("Warehouse not built — run `python -m src.etl.run_pipeline` first.")
    return warehouse


@pytest.fixture(scope="module")
def q(wh):
    def run(sql: str) -> pd.DataFrame:
        return pd.read_sql(sql, wh.engine)
    return run


def test_pipeline_quality_checks_all_pass(wh):
    report = run_checks(wh)
    failures = [o.name for o in report.outcomes if not o.passed]
    assert not failures, f"failing checks: {failures}"


def test_order_grain_is_unique(q):
    row = q("SELECT COUNT(*) AS n, COUNT(DISTINCT order_id) AS d FROM fact_orders").iloc[0]
    assert row["n"] == row["d"] == 99_441


def test_line_item_grain_is_unique(q):
    row = q("SELECT COUNT(*) AS n, COUNT(DISTINCT order_item_key) AS d "
            "FROM fact_order_items").iloc[0]
    assert row["n"] == row["d"] == 112_650


def test_revenue_reconciles_between_grains(q):
    """The classic fan-out bug: order revenue must equal line-item revenue."""
    orders = q("SELECT SUM(gross_revenue) AS v FROM fact_orders").iloc[0]["v"]
    items = q("SELECT SUM(item_gross_value) AS v FROM fact_order_items").iloc[0]["v"]
    assert abs(orders - items) < 1.0


def test_payments_do_not_inflate_revenue(q):
    """fact_payments has more rows than fact_orders; joining it must not be the
    path to revenue. Guard the invariant that makes that safe."""
    payments = q("SELECT COUNT(*) AS n FROM fact_payments").iloc[0]["n"]
    orders = q("SELECT COUNT(*) AS n FROM fact_orders").iloc[0]["n"]
    assert payments > orders


def test_customer_dimension_is_person_grain(q):
    """dim_customer must collapse the per-order customer_id to real people."""
    people = q("SELECT COUNT(*) AS n FROM dim_customer").iloc[0]["n"]
    order_keys = q("SELECT COUNT(DISTINCT source_customer_id) AS n FROM fact_orders").iloc[0]["n"]
    assert people == 96_096
    assert people < order_keys


def test_repeat_rate_is_non_zero(q):
    """If this hits zero the customer key regressed to the per-order id."""
    pct = q("SELECT 100.0 * SUM(is_repeat_customer) / COUNT(*) AS p "
            "FROM dim_customer").iloc[0]["p"]
    assert 2.0 < pct < 5.0


def test_no_state_maps_to_two_regions(q):
    """A state split across regions silently corrupts every regional total."""
    dup = q("""
        SELECT state_code FROM (
            SELECT state_code, COUNT(DISTINCT region) AS r
            FROM dim_customer GROUP BY state_code
        ) t WHERE r > 1
    """)
    assert dup.empty


def test_date_spine_has_no_gaps(q):
    row = q("SELECT COUNT(*) AS n, MIN(calendar_date) AS lo, MAX(calendar_date) AS hi "
            "FROM dim_date").iloc[0]
    span = (pd.Timestamp(row["hi"]) - pd.Timestamp(row["lo"])).days + 1
    assert row["n"] == span, "dim_date is not contiguous"


def test_cohort_month_zero_is_always_full(q):
    values = q("SELECT DISTINCT retention_pct FROM mart_cohort_retention "
               "WHERE months_since_first_order = 0")["retention_pct"]
    assert (values == 100.0).all()


def test_rfm_scores_are_in_range(q):
    row = q("""
        SELECT MIN(r_score) AS rmin, MAX(r_score) AS rmax,
               MIN(f_score) AS fmin, MAX(f_score) AS fmax,
               MIN(m_score) AS mmin, MAX(m_score) AS mmax
        FROM mart_rfm
    """).iloc[0]
    for lo, hi in [(row["rmin"], row["rmax"]), (row["fmin"], row["fmax"]),
                   (row["mmin"], row["mmax"])]:
        assert 1 <= lo <= hi <= 5


def test_funnel_is_monotonically_narrowing(q):
    """Each stage must be a subset of the one before it."""
    stages = q("""
        SELECT stage_order, SUM(orders) AS orders
        FROM mart_order_funnel GROUP BY stage_order ORDER BY stage_order
    """)["orders"].tolist()
    assert stages == sorted(stages, reverse=True)


def test_category_pareto_is_cumulative(q):
    cum = q("SELECT cumulative_revenue_pct FROM mart_category_performance "
            "ORDER BY revenue_rank")["cumulative_revenue_pct"].tolist()
    assert cum == sorted(cum)
    assert 99.0 < cum[-1] <= 100.01


def test_agent_schema_hides_staging_tables(wh):
    """The agent must not be able to reach the un-modelled staging layer."""
    from src.llm.schema_context import build_briefing

    briefing = build_briefing(wh)
    assert briefing.tables
    assert not any(t.startswith("stg_") for t in briefing.tables)
    assert "fact_orders" in briefing.tables


def test_query_builder_suggestions_all_execute(wh):
    """Every suggested question must parse and run against the current schema.

    These are the questions offered in the app's UI. A schema change that breaks
    one of them would otherwise only surface as an error in front of a user.
    """
    from src.llm import query_builder
    from src.llm.agent import DataAgent

    agent = DataAgent(wh, prefer="none")       # builder only: no model, no network
    for question in query_builder.SUGGESTIONS:
        run = agent.ask(question)
        assert run.understood, f"builder could not parse its own suggestion: {question}"
        assert run.steps, f"no query produced for: {question}"
        assert run.steps[0].ok, f"query failed for {question}: {run.steps[0].error}"
        assert run.steps[0].rows > 0, f"no rows for: {question}"


# --- dashboard cube -------------------------------------------------------
# The dashboard filters read pre-computed 'ALL' rollup rows rather than adding
# up region rows in the browser, because distinct customer counts are not
# additive. These tests are what make that shortcut safe.

def test_dash_kpi_all_matches_fact_orders(q):
    k = q("SELECT * FROM mart_dash_kpi WHERE region='ALL' AND year_label='ALL'").iloc[0]
    t = q("""
        SELECT COUNT(*) AS orders, SUM(is_valid_sale) AS valid,
               COUNT(DISTINCT customer_key) AS customers,
               SUM(CASE WHEN is_valid_sale=1 THEN gross_revenue ELSE 0 END) AS revenue
        FROM fact_orders
    """).iloc[0]
    assert k["orders"] == t["orders"]
    assert k["valid_orders"] == t["valid"]
    assert k["customers"] == t["customers"]
    assert abs(k["revenue"] - t["revenue"]) < 0.01


def test_dash_kpi_regions_sum_to_all(q):
    df = q("SELECT region, year_label, orders, revenue FROM mart_dash_kpi")
    for year in df["year_label"].unique():
        sub = df[df["year_label"] == year]
        total = sub[sub["region"] == "ALL"]
        parts = sub[sub["region"] != "ALL"]
        assert total["orders"].iloc[0] == parts["orders"].sum(), year
        assert abs(total["revenue"].iloc[0] - parts["revenue"].sum()) < 0.01, year


def test_dash_monthly_all_rollup_is_consistent(q):
    df = q("SELECT region, revenue, orders FROM mart_dash_monthly")
    allrows = df[df["region"] == "ALL"]
    parts = df[df["region"] != "ALL"]
    assert abs(allrows["revenue"].sum() - parts["revenue"].sum()) < 0.01
    assert allrows["orders"].sum() == parts["orders"].sum()


def test_dash_category_has_no_synthetic_other_bucket(q):
    """'other' would become the largest bar and destroy the Pareto reading."""
    cats = q("SELECT DISTINCT category FROM mart_dash_category")["category"].tolist()
    assert "other" not in cats


def test_dash_category_reconciles_to_line_items(q):
    cube = q("SELECT SUM(revenue) AS v FROM mart_dash_category WHERE region='ALL'").iloc[0]["v"]
    items = q("""
        SELECT SUM(item_gross_value) AS v FROM fact_order_items
        WHERE order_status NOT IN ('canceled','unavailable')
    """).iloc[0]["v"]
    assert abs(cube - items) < 1.0


def test_dash_cohort_month_zero_is_full_in_every_region(q):
    df = q("""
        SELECT region, MIN(retention_pct) AS lo, MAX(retention_pct) AS hi
        FROM mart_dash_cohort WHERE months_since_first_order = 0 GROUP BY region
    """)
    assert (df["lo"] == 100.0).all() and (df["hi"] == 100.0).all()


def test_dash_funnel_narrows_in_every_region(q):
    df = q("""
        SELECT region, stage_order, SUM(orders) AS orders
        FROM mart_dash_funnel GROUP BY region, stage_order
    """)
    for region, grp in df.groupby("region"):
        vals = grp.sort_values("stage_order")["orders"].tolist()
        assert vals == sorted(vals, reverse=True), region


def test_dash_delivery_weighted_review_matches_national_finding(q):
    df = q("""
        SELECT bucket, SUM(orders) AS orders,
               SUM(review_score_sum) / SUM(review_count) AS review
        FROM mart_dash_delivery WHERE region='ALL' GROUP BY bucket, bucket_order
        ORDER BY bucket_order
    """)
    fast = df[df["bucket"] == "0-3 days"].iloc[0]["review"]
    slow = df[df["bucket"] == "30+ days"].iloc[0]["review"]
    assert round(fast, 2) == 4.46
    assert round(slow, 2) == 2.19
    assert fast > slow


def test_dash_rfm_regions_sum_to_all(q):
    df = q("SELECT region, customers FROM mart_dash_rfm")
    total = df[df["region"] == "ALL"]["customers"].sum()
    parts = df[df["region"] != "ALL"]["customers"].sum()
    assert total == parts


def test_dash_geo_has_coordinates_for_every_state(q):
    missing = q("SELECT COUNT(*) AS n FROM mart_dash_geo WHERE latitude IS NULL").iloc[0]["n"]
    assert missing == 0


def test_every_tableau_extract_the_workbook_expects_exists():
    """The workbook and the extract list must not drift apart.

    They did once: swapping an extract for a differently-named one broke the
    workbook builder, and nothing caught it until the build crashed.
    """
    from src.config import EXTRACT_DIR
    from src.etl.export_tableau import SHEETS

    missing = [name for name in SHEETS if not (EXTRACT_DIR / f"{name}.csv").exists()]
    assert not missing, f"workbook expects extracts that were not produced: {missing}"


def test_rfm_segmentation_is_deterministic(wh, q):
    """Rebuilding mart_rfm must produce identical scores.

    NTILE has to split ties somewhere, and thousands of customers share an
    identical recency or spend. Without a deterministic tiebreaker in the
    ORDER BY, the split follows whatever row order the engine happens to
    produce — PostgreSQL and SQLite disagreed by a couple of customers per
    segment on the same data. A segmentation that changes when you re-run it
    cannot be used to target anyone.
    """
    from src.etl.sql_runner import run_models

    before = q("SELECT customer_key, r_score, f_score, m_score, rfm_segment "
               "FROM mart_rfm ORDER BY customer_key")
    run_models(wh, select="mart_rfm")
    after = q("SELECT customer_key, r_score, f_score, m_score, rfm_segment "
              "FROM mart_rfm ORDER BY customer_key")

    assert len(before) == len(after)
    changed = (before["rfm_segment"].values != after["rfm_segment"].values).sum()
    assert changed == 0, f"{changed} customers changed segment on a plain rebuild"
