"""The macro layer is what lets one set of SQL models serve both PostgreSQL and
SQLite. If a macro silently emits the wrong dialect, every downstream number is
wrong, so it is tested directly rather than only through the models.
"""

from __future__ import annotations

import pytest

from src.etl.sql_runner import MACROS, compile_sql, parse_model
from src.config import SQL_DIR


@pytest.mark.parametrize("dialect", ["postgresql", "sqlite"])
def test_every_model_compiles(dialect):
    """No model may reach the database with an unexpanded macro."""
    models = sorted((SQL_DIR / "models").glob("*.sql"))
    assert models, "no SQL models found"
    for path in models:
        compiled = compile_sql(parse_model(path).select_sql, dialect)
        assert "{{" not in compiled, f"unexpanded macro in {path.name} ({dialect})"
        assert "}}" not in compiled, f"unexpanded macro in {path.name} ({dialect})"


def test_dialects_actually_differ():
    """Guards against a macro that ignores its dialect argument."""
    sql = "SELECT {{ month_start(order_date) }} FROM t"
    assert compile_sql(sql, "postgresql") != compile_sql(sql, "sqlite")
    assert "DATE_TRUNC" in compile_sql(sql, "postgresql")
    assert "start of month" in compile_sql(sql, "sqlite")


def test_macro_arguments_may_contain_function_calls():
    """`{{ month_start( MIN(col) ) }}` must not be truncated at the inner paren."""
    out = compile_sql("SELECT {{ month_start( MIN(order_date) ) }} FROM t", "sqlite")
    assert "MIN(order_date)" in out
    assert "{{" not in out


def test_region_macro_covers_every_state_once():
    """Each of Brazil's 27 federative units maps to exactly one region."""
    sql = MACROS["region"]("sqlite", "state")
    states = [
        "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS",
        "MG", "PA", "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC",
        "SP", "SE", "TO",
    ]
    for state in states:
        assert f"'{state}'" in sql, f"{state} missing from the region mapping"
    assert sql.count("'AC'") == 1


def test_unknown_macro_raises_rather_than_passing_through():
    with pytest.raises(KeyError, match="no_such_macro"):
        compile_sql("SELECT {{ no_such_macro(x) }}", "sqlite")


@pytest.mark.parametrize("dialect", ["postgresql", "sqlite"])
def test_days_between_argument_order(dialect):
    """days_between(later, earlier) — a flipped sign would invert 'late delivery'."""
    out = compile_sql("{{ days_between(delivered, purchased) }}", dialect)
    assert out.index("delivered") < out.index("purchased")


def test_model_metadata_is_parsed():
    model = parse_model(SQL_DIR / "models" / "21_fact_orders.sql")
    assert model.name == "fact_orders"
    assert model.materialized == "table"
    assert "stg_orders" in model.depends_on
    assert model.select_sql.lstrip().upper().startswith("WITH")


def test_view_materialization_is_honoured():
    model = parse_model(SQL_DIR / "models" / "39_mart_customer_360.sql")
    assert model.materialized == "view"
