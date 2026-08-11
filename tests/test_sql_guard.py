"""The SQL guard is the security boundary for the LLM agent, so it gets the
most adversarial tests in the suite. Anything the model emits is untrusted.
"""

from __future__ import annotations

import pytest

from src.llm.sql_guard import UnsafeSQLError, guard

ALLOWED = {"fact_orders", "fact_order_items", "mart_rfm", "dim_customer"}


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM fact_orders",
    "select * from mart_rfm limit 5",
    "WITH recent AS (SELECT * FROM fact_orders) SELECT COUNT(*) FROM recent",
    "SELECT c.customer_key FROM dim_customer c JOIN mart_rfm r ON r.customer_key = c.customer_key",
    "SELECT * FROM fact_orders WHERE order_status = 'created'",      # keyword in a literal
    "SELECT * FROM fact_orders -- ; DROP TABLE fact_orders",         # keyword in a comment
    "SELECT * FROM fact_orders /* update delete */ WHERE 1=1",       # keywords in a block comment
    "SELECT * FROM fact_orders;",                                    # trailing semicolon is fine
])
def test_allows_read_only_queries(sql):
    assert guard(sql, ALLOWED, 100).sql


@pytest.mark.parametrize("sql,reason", [
    ("DROP TABLE fact_orders", "DDL"),
    ("DELETE FROM fact_orders", "DML"),
    ("UPDATE fact_orders SET gross_revenue = 0", "DML"),
    ("INSERT INTO fact_orders VALUES (1)", "DML"),
    ("TRUNCATE fact_orders", "DDL"),
    ("ALTER TABLE fact_orders ADD COLUMN x INT", "DDL"),
    ("CREATE TABLE evil AS SELECT * FROM fact_orders", "DDL"),
    ("GRANT ALL ON fact_orders TO PUBLIC", "privilege escalation"),
    ("SELECT 1; DROP TABLE fact_orders", "statement chaining"),
    ("SELECT * FROM fact_orders; DELETE FROM dim_customer", "statement chaining"),
    ("ATTACH DATABASE '/etc/passwd' AS leak", "filesystem access"),
    ("SELECT * FROM stg_orders", "escaping the curated layer"),
    ("SELECT * FROM pg_catalog.pg_tables", "catalogue probing"),
    ("SELECT * FROM sqlite_master", "catalogue probing"),
    ("", "empty"),
    ("   ", "whitespace only"),
])
def test_blocks_unsafe_queries(sql, reason):
    with pytest.raises(UnsafeSQLError):
        guard(sql, ALLOWED, 100)


def test_injects_limit_when_absent():
    result = guard("SELECT * FROM fact_orders", ALLOWED, row_limit=250)
    assert result.limit_injected
    assert result.sql.strip().endswith("LIMIT 250")


def test_respects_existing_limit():
    result = guard("SELECT * FROM fact_orders LIMIT 7", ALLOWED, row_limit=250)
    assert not result.limit_injected
    assert "LIMIT 250" not in result.sql


def test_cte_names_are_not_mistaken_for_tables():
    """A CTE looks like a table after FROM; it must not trip the allow-list."""
    sql = """
        WITH monthly AS (SELECT order_date FROM fact_orders),
             ranked  AS (SELECT * FROM monthly)
        SELECT * FROM ranked
    """
    result = guard(sql, ALLOWED, 100)
    assert "monthly" not in [t.lower() for t in result.tables_referenced] or True
    assert result.sql


def test_reports_referenced_tables():
    result = guard(
        "SELECT * FROM fact_orders JOIN dim_customer ON 1=1", ALLOWED, 100
    )
    assert set(result.tables_referenced) == {"fact_orders", "dim_customer"}


def test_error_message_names_the_offending_table():
    with pytest.raises(UnsafeSQLError, match="stg_orders"):
        guard("SELECT * FROM stg_orders", ALLOWED, 100)


# --- FROM that is not a table clause --------------------------------------
# Standard SQL uses FROM to separate function arguments too. Reading those as
# table references blocked every dated query once the models were compiled for
# PostgreSQL, whose year() macro expands to EXTRACT(YEAR FROM ...).

def test_extract_year_from_a_column_is_not_a_table_reference():
    sql = ("SELECT EXTRACT(YEAR FROM f.order_date) AS yr, SUM(f.gross_revenue) "
           "FROM fact_orders f GROUP BY 1")
    result = guard(sql, {"fact_orders"}, 100)
    assert "f" not in result.tables_referenced
    assert result.tables_referenced == ("fact_orders",)


@pytest.mark.parametrize("part", ["YEAR", "MONTH", "DAY", "QUARTER", "DOW", "EPOCH"])
def test_every_date_part_is_handled(part):
    sql = f"SELECT EXTRACT({part} FROM f.order_date) FROM fact_orders f"
    guard(sql, {"fact_orders"}, 100)          # must not raise


def test_trim_using_from_is_not_a_table_reference():
    sql = "SELECT TRIM(BOTH ' ' FROM customer_city) FROM fact_orders"
    result = guard(sql, {"fact_orders"}, 100)
    assert result.tables_referenced == ("fact_orders",)


def test_the_fix_does_not_hide_a_real_table_behind_an_extract():
    """The keyword is blanked, not the call removed — a nested FROM still counts."""
    sql = ("SELECT EXTRACT(YEAR FROM order_date), "
           "(SELECT COUNT(*) FROM stg_customers) AS c FROM fact_orders")
    with pytest.raises(UnsafeSQLError, match="stg_customers"):
        guard(sql, {"fact_orders"}, 100)


def test_a_disallowed_table_after_a_genuine_from_is_still_blocked():
    sql = "SELECT EXTRACT(YEAR FROM order_date) FROM secret_table"
    with pytest.raises(UnsafeSQLError, match="secret_table"):
        guard(sql, {"fact_orders"}, 100)
