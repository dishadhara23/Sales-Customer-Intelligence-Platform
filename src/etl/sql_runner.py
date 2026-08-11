"""A minimal, dbt-style SQL model runner.

Why this exists
---------------
The warehouse target is PostgreSQL, but the project must also run on the SQLite
fallback (see :mod:`src.config`). Rather than maintaining two copies of every
model, each model in ``sql/models`` is written once against a small macro
vocabulary and compiled per dialect at run time.

Model file format
-----------------
A model is a single ``SELECT``. Metadata lives in leading ``--`` comments::

    -- model: dim_customer
    -- materialized: table
    -- depends_on: stg_customers, stg_orders
    -- description: One row per distinct person (customer_unique_id).
    SELECT ...

``model`` defaults to the filename stem (minus any numeric prefix).
``materialized`` is ``table`` (default) or ``view``.
Execution order is the lexical order of filenames, so prefix them ``01_``,
``02_``, ... ; ``depends_on`` is validated against what has already been built
and raises early on a bad ordering rather than failing deep inside the SQL.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import text

from src.config import SQL_DIR, Warehouse

log = logging.getLogger(__name__)

MACRO_CALL = re.compile(r"\{\{\s*(\w+)\s*\((.*?)\)\s*\}\}", re.DOTALL)
META_LINE = re.compile(r"^--\s*(model|materialized|depends_on|description)\s*:\s*(.+?)\s*$", re.I)


# ---------------------------------------------------------------------------
# Dialect macros
# ---------------------------------------------------------------------------
# Each macro maps (dialect, *args) -> SQL fragment. Args arrive as raw strings
# exactly as written in the model, so a column reference and a quoted literal
# both pass through untouched.

def _m_date(dialect: str, col: str) -> str:
    """Truncate a timestamp to a date."""
    return f"CAST({col} AS DATE)" if dialect == "postgresql" else f"DATE({col})"


def _m_month_start(dialect: str, col: str) -> str:
    """First day of the month containing ``col``."""
    if dialect == "postgresql":
        return f"CAST(DATE_TRUNC('month', {col}) AS DATE)"
    return f"DATE({col}, 'start of month')"


def _m_year_month(dialect: str, col: str) -> str:
    """``YYYY-MM`` label."""
    if dialect == "postgresql":
        return f"TO_CHAR({col}, 'YYYY-MM')"
    return f"STRFTIME('%Y-%m', {col})"


def _m_year(dialect: str, col: str) -> str:
    if dialect == "postgresql":
        return f"CAST(EXTRACT(YEAR FROM {col}) AS INTEGER)"
    return f"CAST(STRFTIME('%Y', {col}) AS INTEGER)"


def _m_month(dialect: str, col: str) -> str:
    if dialect == "postgresql":
        return f"CAST(EXTRACT(MONTH FROM {col}) AS INTEGER)"
    return f"CAST(STRFTIME('%m', {col}) AS INTEGER)"


def _m_quarter(dialect: str, col: str) -> str:
    if dialect == "postgresql":
        return f"CAST(EXTRACT(QUARTER FROM {col}) AS INTEGER)"
    return f"((CAST(STRFTIME('%m', {col}) AS INTEGER) - 1) / 3 + 1)"


def _m_dow(dialect: str, col: str) -> str:
    """Day of week, 0 = Sunday .. 6 = Saturday (both dialects agree here)."""
    if dialect == "postgresql":
        return f"CAST(EXTRACT(DOW FROM {col}) AS INTEGER)"
    return f"CAST(STRFTIME('%w', {col}) AS INTEGER)"


def _m_days_between(dialect: str, later: str, earlier: str) -> str:
    """Whole days from ``earlier`` to ``later`` (negative if reversed)."""
    if dialect == "postgresql":
        return f"(CAST({later} AS DATE) - CAST({earlier} AS DATE))"
    return f"CAST(JULIANDAY(DATE({later})) - JULIANDAY(DATE({earlier})) AS INTEGER)"


def _m_months_between(dialect: str, later: str, earlier: str) -> str:
    """Whole calendar months between two dates — the cohort index primitive."""
    if dialect == "postgresql":
        return (
            f"((CAST(EXTRACT(YEAR FROM {later}) AS INTEGER) - CAST(EXTRACT(YEAR FROM {earlier}) AS INTEGER)) * 12"
            f" + (CAST(EXTRACT(MONTH FROM {later}) AS INTEGER) - CAST(EXTRACT(MONTH FROM {earlier}) AS INTEGER)))"
        )
    return (
        f"((CAST(STRFTIME('%Y', {later}) AS INTEGER) - CAST(STRFTIME('%Y', {earlier}) AS INTEGER)) * 12"
        f" + (CAST(STRFTIME('%m', {later}) AS INTEGER) - CAST(STRFTIME('%m', {earlier}) AS INTEGER)))"
    )


def _m_add_days(dialect: str, col: str, n: str) -> str:
    """Shift a date by ``n`` days, returning a DATE — the date-spine primitive."""
    if dialect == "postgresql":
        return f"CAST(CAST({col} AS DATE) + ({n}) * INTERVAL '1 day' AS DATE)"
    return f"DATE({col}, '+' || CAST({n} AS TEXT) || ' day')"


def _m_date_key(dialect: str, col: str) -> str:
    """Integer surrogate key in ``YYYYMMDD`` form."""
    if dialect == "postgresql":
        return f"CAST(TO_CHAR({col}, 'YYYYMMDD') AS INTEGER)"
    return f"CAST(STRFTIME('%Y%m%d', {col}) AS INTEGER)"


_BRAZIL_REGIONS = {
    "North": ("AC", "AP", "AM", "PA", "RO", "RR", "TO"),
    "Northeast": ("AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"),
    "Central-West": ("DF", "GO", "MT", "MS"),
    "Southeast": ("ES", "MG", "RJ", "SP"),
    "South": ("PR", "RS", "SC"),
}


def _m_region(_dialect: str, col: str) -> str:
    """Map a two-letter Brazilian state code to its macro-region.

    Defined once here rather than repeated as a CASE in each model: customer,
    seller and geography dimensions must all agree, or a state ends up split
    across two "regions" and every regional total is quietly wrong.
    """
    whens = " ".join(
        f"WHEN UPPER({col}) IN ({', '.join(repr(s) for s in states)}) THEN '{region}'"
        for region, states in _BRAZIL_REGIONS.items()
    )
    return f"CASE {whens} ELSE 'Unknown' END"


def _m_num(dialect: str, expr: str) -> str:
    """Cast to a floating-point number (guards integer division)."""
    return f"CAST({expr} AS DOUBLE PRECISION)" if dialect == "postgresql" else f"CAST({expr} AS REAL)"


def _m_round2(dialect: str, expr: str) -> str:
    if dialect == "postgresql":
        return f"ROUND(CAST({expr} AS NUMERIC), 2)"
    return f"ROUND({expr}, 2)"


MACROS = {
    "date": _m_date,
    "month_start": _m_month_start,
    "year_month": _m_year_month,
    "year": _m_year,
    "month": _m_month,
    "quarter": _m_quarter,
    "dow": _m_dow,
    "days_between": _m_days_between,
    "months_between": _m_months_between,
    "add_days": _m_add_days,
    "date_key": _m_date_key,
    "num": _m_num,
    "round2": _m_round2,
    "region": _m_region,
}


def _split_args(raw: str) -> list[str]:
    """Split macro arguments on top-level commas (parens/quotes aware)."""
    args, depth, buf, quote = [], 0, [], None
    for ch in raw:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch in "([":
            depth += 1
            buf.append(ch)
        elif ch in ")]":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            args.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        args.append("".join(buf).strip())
    return [a for a in args if a]


def compile_sql(sql: str, dialect: str) -> str:
    """Expand every ``{{ macro(...) }}`` call for the target dialect."""

    def replace(match: re.Match[str]) -> str:
        name, raw_args = match.group(1), match.group(2)
        if name not in MACROS:
            raise KeyError(f"Unknown SQL macro {{{{ {name}(...) }}}}. Known: {sorted(MACROS)}")
        return MACROS[name](dialect, *_split_args(raw_args))

    # Loop so macros nested inside macro arguments resolve too.
    for _ in range(5):
        new_sql = MACRO_CALL.sub(replace, sql)
        if new_sql == sql:
            return new_sql
        sql = new_sql
    raise RecursionError("Macro expansion did not converge after 5 passes.")


@dataclass
class Model:
    name: str
    materialized: str
    depends_on: list[str]
    description: str
    select_sql: str
    path: Path
    rows: int = 0
    seconds: float = 0.0


@dataclass
class RunResult:
    models: list[Model] = field(default_factory=list)

    def summary(self) -> str:
        width = max((len(m.name) for m in self.models), default=10)
        lines = [f"{'model'.ljust(width)}  {'rows':>9}  {'mat':<5}  secs"]
        lines.append("-" * (width + 26))
        for m in self.models:
            lines.append(f"{m.name.ljust(width)}  {m.rows:>9,}  {m.materialized:<5}  {m.seconds:.2f}")
        return "\n".join(lines)


def parse_model(path: Path) -> Model:
    raw = path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body_lines: list[str] = []
    in_header = True
    last_key: str | None = None

    for line in raw.splitlines():
        if in_header:
            if m := META_LINE.match(line):
                last_key = m.group(1).lower()
                meta[last_key] = m.group(2)
                continue
            # A bare `--` line inside the header continues the previous value,
            # so a wrapped `-- description:` stays metadata instead of leaking
            # into the SQL body.
            if last_key and line.strip().startswith("--"):
                meta[last_key] += " " + line.strip().lstrip("-").strip()
                continue
            if not line.strip():
                continue
            in_header = False
        body_lines.append(line)

    default_name = re.sub(r"^\d+_", "", path.stem)
    return Model(
        name=meta.get("model", default_name),
        materialized=meta.get("materialized", "table").lower(),
        depends_on=[d.strip() for d in meta.get("depends_on", "").split(",") if d.strip()],
        description=meta.get("description", ""),
        select_sql="\n".join(body_lines).strip().rstrip(";"),
        path=path,
    )


def relation_type(conn, dialect: str, name: str) -> str | None:
    """Return ``'table'``, ``'view'`` or ``None`` for an existing relation.

    Both engines reject ``DROP VIEW`` against a table (and vice versa), so the
    kind has to be looked up rather than guessed — a model that changes from
    ``materialized: table`` to ``view`` must still rebuild cleanly.
    """
    if dialect == "postgresql":
        row = conn.execute(
            text(
                "SELECT CASE c.relkind WHEN 'v' THEN 'view' ELSE 'table' END "
                "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = current_schema() AND c.relname = :n "
                "AND c.relkind IN ('r', 'v', 'm', 'p')"
            ),
            {"n": name},
        ).scalar()
    else:
        row = conn.execute(
            text("SELECT type FROM sqlite_master WHERE name = :n AND type IN ('table','view')"),
            {"n": name},
        ).scalar()
    return row


def drop_relation(conn, dialect: str, name: str) -> None:
    """Drop ``name`` whatever kind it currently is."""
    kind = relation_type(conn, dialect, name)
    if kind is None:
        return
    # CASCADE so a downstream view does not block rebuilding its base table.
    cascade = " CASCADE" if dialect == "postgresql" else ""
    conn.execute(text(f"DROP {kind.upper()} IF EXISTS {name}{cascade}"))


def _list_tables_sql(dialect: str):
    if dialect == "postgresql":
        return text(
            "SELECT tablename AS name FROM pg_tables WHERE schemaname = 'public' "
            "UNION SELECT viewname FROM pg_views WHERE schemaname = 'public'"
        )
    return text("SELECT name FROM sqlite_master WHERE type IN ('table','view')")

def run_models(wh: Warehouse, models_dir: Path | None = None, select: str | None = None) -> RunResult:
    """Build every SQL model in ``models_dir`` against ``wh``.

    ``select`` optionally filters to models whose name contains that substring.
    """
    models_dir = models_dir or (SQL_DIR / "models")
    paths = sorted(models_dir.glob("*.sql"))
    if not paths:
        raise FileNotFoundError(f"No .sql models found in {models_dir}")

    result = RunResult()
    built: set[str] = set()

    # Staging tables are loaded by the extract/load step, not by a model file.
    with wh.engine.connect() as conn:
        insp_tables = {row[0] for row in conn.execute(_list_tables_sql(wh.dialect))}
    built |= insp_tables

    for path in paths:
        model = parse_model(path)
        if select and select not in model.name:
            continue

        missing = [d for d in model.depends_on if d not in built]
        if missing:
            raise RuntimeError(
                f"Model '{model.name}' ({path.name}) depends on {missing}, which have not been "
                "built yet. Check the numeric filename prefixes control the ordering."
            )

        compiled = compile_sql(model.select_sql, wh.dialect)
        started = time.perf_counter()

        with wh.engine.begin() as conn:
            drop_relation(conn, wh.dialect, model.name)
            keyword = "VIEW" if model.materialized == "view" else "TABLE"
            conn.execute(text(f"CREATE {keyword} {model.name} AS\n{compiled}"))
            model.rows = conn.execute(text(f"SELECT COUNT(*) FROM {model.name}")).scalar_one()

        model.seconds = time.perf_counter() - started
        built.add(model.name)
        result.models.append(model)
        log.info("built %-28s %9s rows  (%.2fs)", model.name, f"{model.rows:,}", model.seconds)

    return result
