"""Validate model-generated SQL before it reaches the warehouse.

The agent is given a tool that runs SQL. Everything it emits is untrusted
input, so this module is the security boundary — not the system prompt. A
prompt can be talked around; this cannot.

Policy
------
1. Exactly one statement (no ``;`` chaining).
2. Must start with ``SELECT`` or ``WITH``.
3. No DDL/DML keyword anywhere as a bare word.
4. No access to tables outside the curated allow-list.
5. A ``LIMIT`` is injected if the query does not have one.

Layer 5 of the defence is outside this module: the database user itself should
be read-only in production. Application-level checks are the second line, not
the first.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Bare-word match so that a column innocently named `updated_at` or a category
# containing "create" does not trip the filter.
FORBIDDEN_KEYWORDS = (
    "insert", "update", "delete", "drop", "truncate", "alter", "create",
    "grant", "revoke", "replace", "attach", "detach", "vacuum", "pragma",
    "copy", "call", "merge", "commit", "rollback", "savepoint", "set",
)

_FORBIDDEN_RE = re.compile(
    r"\b(" + "|".join(FORBIDDEN_KEYWORDS) + r")\b", re.IGNORECASE
)
_COMMENT_RE = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_STRING_RE = re.compile(r"'(?:[^']|'')*'")
_LIMIT_RE = re.compile(r"\blimit\b\s+\d+", re.IGNORECASE)
_TABLE_REF_RE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)

# `FROM` is not always a table clause. In standard SQL it also separates the
# arguments of EXTRACT and TRIM:
#
#     EXTRACT(YEAR FROM f.order_date)
#     TRIM(BOTH ' ' FROM name)
#
# Read naively, the first of those says the query selects from a table called
# `f`, and the guard rejects a perfectly ordinary query. This is not
# hypothetical — it blocked every dated query the moment the models were
# compiled for PostgreSQL, whose `year()` macro expands to exactly that.
#
# The keyword is blanked rather than the call being removed: deleting the body
# could hide a real `FROM some_table` inside a nested subquery, which would turn
# a cosmetic bug into a hole in the allow-list.
_DATE_PARTS = (
    "century", "day", "decade", "dow", "doy", "epoch", "hour", "isodow",
    "isoyear", "microseconds", "millennium", "milliseconds", "minute", "month",
    "quarter", "second", "timezone", "timezone_hour", "timezone_minute",
    "week", "year",
)
_FROM_AS_SEPARATOR_RE = re.compile(
    r"\b(?:" + "|".join(_DATE_PARTS) + r"|leading|trailing|both)\b"   # what precedes it
    r"(?P<between>(?:'(?:[^']|'')*'|[^'()])*?)"                      # args, no nesting
    r"\bfrom\b",
    re.IGNORECASE,
)


def _blank_separator_froms(sql: str) -> str:
    """Neutralise `FROM` where it separates function arguments, not tables."""
    return _FROM_AS_SEPARATOR_RE.sub(
        lambda m: m.group(0)[: -len("from")] + " " * len("from"), sql
    )


class UnsafeSQLError(ValueError):
    """Raised when generated SQL violates the read-only policy."""


@dataclass(frozen=True)
class GuardResult:
    sql: str
    limit_injected: bool
    tables_referenced: tuple[str, ...]


def _strip_noise(sql: str) -> str:
    """Remove comments and string literals so keyword scanning sees only code."""
    return _STRING_RE.sub("''", _COMMENT_RE.sub(" ", sql))


def guard(sql: str, allowed_tables: set[str], row_limit: int = 1000) -> GuardResult:
    raw = sql.strip().rstrip(";").strip()
    if not raw:
        raise UnsafeSQLError("Empty query.")

    scannable = _strip_noise(raw)

    if ";" in scannable:
        raise UnsafeSQLError("Only a single statement is allowed (found ';').")

    if not re.match(r"^\s*(select|with)\b", scannable, re.IGNORECASE):
        raise UnsafeSQLError("Query must begin with SELECT or WITH.")

    if match := _FORBIDDEN_RE.search(scannable):
        raise UnsafeSQLError(
            f"Statement type '{match.group(1).upper()}' is not permitted; this "
            "connection is read-only."
        )

    referenced = {
        t.lower() for t in _TABLE_REF_RE.findall(_blank_separator_froms(scannable))
    }
    # CTE names appear after FROM/JOIN too; subtract the ones defined in-query.
    cte_names = {
        m.lower()
        for m in re.findall(
            r"(?:\bwith\b|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(", scannable, re.IGNORECASE
        )
    }
    unknown = referenced - cte_names - {t.lower() for t in allowed_tables}
    if unknown:
        raise UnsafeSQLError(
            f"Query references table(s) outside the curated schema: {sorted(unknown)}. "
            f"Allowed: {sorted(allowed_tables)}"
        )

    limit_injected = False
    if not _LIMIT_RE.search(scannable):
        raw = f"{raw}\nLIMIT {row_limit}"
        limit_injected = True

    return GuardResult(
        sql=raw,
        limit_injected=limit_injected,
        tables_referenced=tuple(sorted(referenced - cte_names)),
    )
