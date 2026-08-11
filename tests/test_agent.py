"""The agent loop: retries, sanity checks, and the fallback to the query builder.

Every test here uses a scripted provider rather than a real model. That is the
point — a test that called Ollama or Claude would be slow, would need a network
or a 5GB download, and would fail for reasons unrelated to the code. The
scripted provider lets the loop's behaviour be asserted exactly: give it a
broken statement and check the agent retries; give it three and check the
question still gets answered.
"""

from __future__ import annotations

import pytest

from src.config import get_warehouse
from src.llm.agent import MAX_ATTEMPTS, DataAgent, sanity_check
from src.llm.providers import Provider, strip_sql

import pandas as pd


@pytest.fixture(scope="module")
def wh():
    return get_warehouse()


class ScriptedProvider(Provider):
    """Returns pre-written SQL, one statement per attempt."""

    name = "scripted"
    label = "Scripted"
    cost_note = "free"

    def __init__(self, *statements: str):
        self.statements = list(statements)
        self.calls: list[str | None] = []

    def available(self) -> bool:
        return True

    def write_sql(self, question: str, schema: str, hint: str | None = None) -> str:
        self.calls.append(hint)
        index = min(len(self.calls) - 1, len(self.statements) - 1)
        return self.statements[index]

    def explain(self, question: str, result_preview: str) -> str:
        return f"Explained: {result_preview.splitlines()[0][:40]}"


GOOD = "SELECT COUNT(*) AS orders FROM fact_orders"
BROKEN = "SELECT nonexistent_column FROM fact_orders"
EMPTY = "SELECT order_id FROM fact_orders WHERE order_status = 'no_such_status'"
UNSAFE = "DROP TABLE fact_orders"


def test_working_sql_is_run_once_and_explained(wh):
    provider = ScriptedProvider(GOOD)
    run = DataAgent(wh, provider=provider).ask("how many orders")
    assert len(run.steps) == 1
    assert run.steps[0].ok
    assert run.steps[0].rows == 1
    assert run.answer.startswith("Explained:")
    assert run.source == "scripted"


def test_a_failed_query_is_retried_with_the_error_as_a_hint(wh):
    """The retry is what makes this agentic — the error goes back to the model."""
    provider = ScriptedProvider(BROKEN, GOOD)
    run = DataAgent(wh, provider=provider).ask("how many orders")

    assert len(run.steps) == 2
    assert not run.steps[0].ok and run.steps[1].ok
    assert provider.calls[0] is None                    # first try gets no hint
    assert "nonexistent_column" in (provider.calls[1] or "")


def test_an_empty_result_is_retried_before_being_reported(wh):
    """Zero rows is usually a wrong literal, which a model can often fix."""
    provider = ScriptedProvider(EMPTY, GOOD)
    run = DataAgent(wh, provider=provider).ask("orders with that status")

    assert len(run.steps) == 2
    assert "zero rows" in (provider.calls[1] or "")
    assert run.steps[1].ok


def test_unsafe_sql_is_blocked_before_it_reaches_the_database(wh):
    provider = ScriptedProvider(UNSAFE, GOOD)
    run = DataAgent(wh, provider=provider).ask("drop everything")

    assert not run.steps[0].ok
    assert run.steps[0].error.startswith("Blocked:")


def test_the_agent_gives_up_after_a_bounded_number_of_attempts(wh):
    """Without a cap, a confused model would loop against a paid API forever."""
    provider = ScriptedProvider(BROKEN)
    run = DataAgent(wh, provider=provider).ask("how many orders")
    model_attempts = [s for s in run.steps if s.purpose.startswith("Attempt")]
    assert len(model_attempts) == MAX_ATTEMPTS


def test_a_model_that_never_succeeds_falls_back_to_the_query_builder(wh):
    """A dead end is a worse answer than a deterministic one."""
    provider = ScriptedProvider(BROKEN)
    run = DataAgent(wh, provider=provider).ask("revenue by region")

    assert run.source == "query_builder", "should have handed off to the builder"
    assert run.answer and "could not produce" not in run.answer
    assert run.last_dataframe is not None and not run.last_dataframe.empty


def test_the_fallback_answer_is_attributed_to_the_builder_not_the_model(wh):
    """Passing off a template's answer as a model's would be dishonest."""
    provider = ScriptedProvider(BROKEN)
    run = DataAgent(wh, provider=provider).ask("revenue by region")
    assert run.source != provider.name


def test_failure_is_reported_when_neither_backend_can_answer(wh):
    provider = ScriptedProvider(BROKEN)
    run = DataAgent(wh, provider=provider).ask("what is the meaning of life")
    assert "could not" in run.answer.lower()


# --- sanity checking: valid SQL that answers the wrong question -------------

def test_negative_delivery_times_are_caught():
    """The classic wrong-column pick: delivery_vs_estimate_days for delivery time."""
    df = pd.DataFrame({"region": ["North"], "avg_days_to_deliver": [-3.2]})
    problem = sanity_check(df)
    assert problem is not None
    assert "days_to_deliver" in problem


def test_negative_revenue_is_caught():
    df = pd.DataFrame({"state": ["SP"], "gross_revenue": [-100.0]})
    assert sanity_check(df) is not None


def test_legitimately_negative_columns_are_not_flagged():
    """Being early is negative and correct — the check must not fire here."""
    df = pd.DataFrame({"region": ["South"], "delivery_vs_estimate_days": [-11.0]})
    assert sanity_check(df) is None


def test_ordinary_results_pass(wh):
    df = pd.DataFrame({"region": ["North", "South"], "avg_days_to_deliver": [22.5, 10.7]})
    assert sanity_check(df) is None


def test_a_sanity_failure_triggers_a_retry(wh):
    """A query can be valid, safe, and still answer the wrong question."""
    wrong = ("SELECT customer_region AS region, "
             "AVG(delivery_vs_estimate_days) AS avg_days_to_deliver "
             "FROM fact_orders WHERE is_valid_sale = 1 GROUP BY customer_region")
    right = ("SELECT customer_region AS region, "
             "AVG(days_to_deliver) AS avg_days_to_deliver "
             "FROM fact_orders WHERE is_valid_sale = 1 GROUP BY customer_region")
    provider = ScriptedProvider(wrong, right)
    run = DataAgent(wh, provider=provider).ask("average delivery time by region")

    assert len(run.steps) == 2
    assert not run.steps[0].ok, "the negative result should have been rejected"
    assert (run.last_dataframe["avg_days_to_deliver"] > 0).all()


# --- extracting SQL from whatever the model wrapped it in ------------------

@pytest.mark.parametrize("raw, expected", [
    ("SELECT 1", "SELECT 1"),
    ("```sql\nSELECT 1\n```", "SELECT 1"),
    ("```\nSELECT 1\n```", "SELECT 1"),
    ("Here's the SQL: SELECT 1", "SELECT 1"),
    ("SELECT 1;", "SELECT 1"),
    ("SELECT 1;\n\nThis counts the rows.", "SELECT 1"),
])
def test_sql_is_recovered_from_conversational_wrapping(raw, expected):
    assert strip_sql(raw) == expected


# --- unlabelled grouped results -------------------------------------------

def test_a_group_by_with_no_label_column_is_rejected(wh):
    """Several rows of bare numbers cannot be read, by a person or by a model."""
    unlabelled = ("SELECT AVG(avg_review_score) AS score FROM "
                  "mart_delivery_performance GROUP BY delivery_speed_bucket")
    labelled = ("SELECT delivery_speed_bucket, AVG(avg_review_score) AS score "
                "FROM mart_delivery_performance GROUP BY delivery_speed_bucket")
    provider = ScriptedProvider(unlabelled, labelled)
    run = DataAgent(wh, provider=provider).ask("review score by delivery speed")

    assert len(run.steps) == 2
    assert not run.steps[0].ok
    assert "delivery_speed_bucket" in run.last_dataframe.columns


def test_a_single_row_aggregate_needs_no_label(wh):
    """"Total revenue" is one number. Demanding a label would be wrong."""
    provider = ScriptedProvider(GOOD)
    run = DataAgent(wh, provider=provider).ask("how many orders")
    assert run.steps[0].ok and len(run.steps) == 1


def test_the_run_names_the_column_the_question_was_about(wh):
    """The chart defaults to this. Guessing charts orders for a question about reviews."""
    agent = DataAgent(wh, prefer="none")
    assert agent.ask("average review score by category").measure == "review"
    assert agent.ask("revenue by region").measure == "revenue"
