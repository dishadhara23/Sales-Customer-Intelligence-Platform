"""Answer a business question: natural language → SQL → numbers → English.

Flow
----
1. A provider (:mod:`src.llm.providers`) writes SQL, or — if none is available —
   the deterministic query builder does.
2. :mod:`src.llm.sql_guard` vets the statement. This is the security boundary,
   and it sits between generation and execution so it cannot be skipped.
3. The SQL runs against the warehouse.
4. If it failed, the error goes back to the provider for one more attempt. That
   retry is what makes this agentic, and it works with a 7B local model as well
   as a frontier API because it needs no vendor tool-calling protocol.
5. The provider describes the result in plain English.

The model never sees warehouse credentials and never gets a write path. It
emits text; this module decides whether that text runs.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd

from src.config import SQL_ROW_LIMIT, Warehouse
from src.etl.sql_runner import compile_sql
from src.llm import query_builder
from src.llm.providers import Provider, get_provider
from src.llm.schema_context import get_briefing
from src.llm.sql_guard import UnsafeSQLError, guard

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MAX_ROWS_TO_MODEL = 40


@dataclass
class QueryStep:
    purpose: str
    sql: str
    ok: bool
    rows: int = 0
    seconds: float = 0.0
    error: str | None = None
    dataframe: pd.DataFrame | None = None
    attempt: int = 1


@dataclass
class AgentRun:
    question: str
    answer: str
    steps: list[QueryStep] = field(default_factory=list)
    seconds: float = 0.0
    source: str = "query_builder"     # which provider produced the SQL
    understood: bool = True
    # The column the question was actually about. The builder knows this
    # outright; guessing it from the result would pick the wrong one whenever a
    # supporting count sits beside the measure.
    measure: str | None = None

    @property
    def last_dataframe(self) -> pd.DataFrame | None:
        for step in reversed(self.steps):
            if step.ok and step.dataframe is not None and not step.dataframe.empty:
                return step.dataframe
        return None


# Columns that cannot legitimately be negative. A negative here means the model
# chose the wrong column — the classic case being delivery_vs_estimate_days
# (early = negative) standing in for days_to_deliver.
NON_NEGATIVE_HINTS = {
    "day": "days_to_deliver", "deliver": "days_to_deliver",
    "revenue": "gross_revenue", "sales": "gross_revenue",
    "count": None, "orders": None, "customers": None, "units": None,
}

# Columns where a negative number is the correct answer, not a mistake. These
# measure a difference, so the sign carries meaning — negative days against the
# estimate means the parcel arrived early. Without this exemption the check
# would reject the very queries it is meant to steer the model towards.
SIGNED_BY_DESIGN = ("vs_estimate", "_diff", "_delta", "_change", "mom_", "yoy_",
                    "growth", "variance")


def sanity_check(df: pd.DataFrame) -> str | None:
    """Return a correction hint if the result is impossible on its face.

    Guarding the SQL is not enough: a query can be valid, safe, and still
    answer the wrong question. Where a value is negative but the concept cannot
    be, that is a detectable mistake, and it is worth one retry.
    """
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            continue
        lowered = col.lower()
        if any(marker in lowered for marker in SIGNED_BY_DESIGN):
            continue
        for token, better in NON_NEGATIVE_HINTS.items():
            if token in lowered and (df[col].dropna() < 0).any():
                fix = f" Use `{better}` instead." if better else ""
                return (f"Column '{col}' came back negative, which is impossible "
                        f"for this measure — you selected the wrong column.{fix}")
    return None


def grouping_is_labelled(sql: str, df: pd.DataFrame) -> str | None:
    """Catch a GROUP BY whose grouping column was never selected.

    The result is several rows of bare numbers with nothing saying what each one
    is. It looks like a successful query, and it is unreadable — worse, a model
    asked to summarise it will guess at the labels from row order.
    """
    if "group by" not in sql.lower() or len(df) < 2:
        return None
    if any(not pd.api.types.is_numeric_dtype(df[c]) for c in df.columns):
        return None
    return ("The result has no label column: you grouped by something but did "
            "not select it, so the rows cannot be told apart. Add the "
            "GROUP BY column to the SELECT list.")


def _preview(df: pd.DataFrame) -> str:
    shown = df.head(MAX_ROWS_TO_MODEL)
    text = shown.to_markdown(index=False)
    if len(df) > MAX_ROWS_TO_MODEL:
        text += f"\n\n({len(df):,} rows total; first {MAX_ROWS_TO_MODEL} shown.)"
    return text


class DataAgent:
    """Answers questions against the warehouse using whatever backend exists."""

    def __init__(self, warehouse: Warehouse, provider: Provider | None = None,
                 row_limit: int = SQL_ROW_LIMIT, prefer: str | None = None):
        self.wh = warehouse
        self.briefing = get_briefing(warehouse)
        self.allowed = set(self.briefing.tables)
        self.row_limit = row_limit
        self.provider = provider if provider is not None else get_provider(prefer)

    @property
    def backend(self) -> str:
        return self.provider.name if self.provider else "query_builder"

    # -- execution ----------------------------------------------------------
    def _run_sql(self, sql: str, purpose: str, attempt: int) -> QueryStep:
        started = time.perf_counter()
        try:
            checked = guard(sql, self.allowed, self.row_limit)
        except UnsafeSQLError as exc:
            return QueryStep(purpose, sql, ok=False, error=f"Blocked: {exc}",
                             attempt=attempt)
        try:
            df = pd.read_sql(checked.sql, self.wh.engine)
        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}".split("\n[SQL")[0][:300]
            return QueryStep(purpose, checked.sql, ok=False, error=msg,
                             seconds=time.perf_counter() - started, attempt=attempt)
        return QueryStep(purpose, checked.sql, ok=True, rows=len(df),
                         seconds=time.perf_counter() - started, dataframe=df,
                         attempt=attempt)

    # -- the two paths ------------------------------------------------------
    def _answer_with_builder(self, question: str, run: AgentRun) -> None:
        plan = query_builder.parse(question)
        if plan is None:
            run.understood = False
            run.answer = query_builder.explain_failure(question)
            return

        sql = compile_sql(query_builder.to_sql(plan, self.row_limit), self.wh.dialect)
        step = self._run_sql(sql, plan.describe(), attempt=1)
        run.steps.append(step)
        if not step.ok:
            run.answer = f"The query failed: {step.error}"
            return
        if step.dataframe is None or step.dataframe.empty:
            run.answer = "That query ran but matched no rows."
            return
        run.measure = plan.metric.key
        run.answer = _describe_without_model(plan, step.dataframe)

    def _answer_with_provider(self, question: str, run: AgentRun) -> None:
        hint: str | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                raw = self.provider.write_sql(question, self.briefing.text, hint)
            except Exception as exc:
                run.answer = (f"The {self.provider.label} backend failed: "
                              f"{type(exc).__name__}: {exc}")
                return

            step = self._run_sql(raw, f"Attempt {attempt}", attempt)
            run.steps.append(step)

            if step.ok and step.dataframe is not None:
                if step.dataframe.empty:
                    # An empty result is usually a wrong literal, which the model
                    # can often fix if told — so it is worth one retry.
                    if attempt < MAX_ATTEMPTS:
                        hint = ("The query ran but returned zero rows. Check the "
                                "literals against the categorical values listed "
                                "in the schema.")
                        continue
                    run.answer = "That query ran but matched no rows."
                    return
                problem = (sanity_check(step.dataframe)
                           or grouping_is_labelled(step.sql, step.dataframe))
                if problem and attempt < MAX_ATTEMPTS:
                    step.ok = False
                    step.error = problem
                    hint = problem
                    continue
                try:
                    run.answer = self.provider.explain(question, _preview(step.dataframe))
                except Exception:
                    run.answer = _describe_dataframe(step.dataframe)
                return

            hint = step.error

        # Every attempt failed. Rather than dead-ending, hand the question to the
        # deterministic builder: a small local model can lose its footing on a
        # join that the builder never has to make. If that works, the answer is
        # attributed to the builder — never passed off as the model's.
        self._fall_back_to_builder(question, run)

    def _fall_back_to_builder(self, question: str, run: AgentRun) -> None:
        failures = list(run.steps)
        rescue = AgentRun(question=question, answer="")
        self._answer_with_builder(question, rescue)

        if rescue.understood and rescue.steps and rescue.steps[-1].ok:
            run.steps = failures + rescue.steps
            run.source = "query_builder"
            run.measure = rescue.measure
            run.answer = rescue.answer
            return

        run.answer = (
            f"I could not produce working SQL after {MAX_ATTEMPTS} attempts, and "
            f"the built-in query builder could not match this question either. "
            f"Last error: {failures[-1].error if failures else 'unknown'}")

    # -- entry point --------------------------------------------------------
    def ask(self, question: str,
            on_event: Callable[[str, object], None] | None = None) -> AgentRun:
        started = time.perf_counter()
        run = AgentRun(question=question, answer="", source=self.backend)
        emit = on_event or (lambda kind, payload: None)
        emit("start", self.backend)

        if self.provider is None:
            self._answer_with_builder(question, run)
        else:
            self._answer_with_provider(question, run)

        run.seconds = time.perf_counter() - started
        emit("done", run)
        return run


# ---------------------------------------------------------------------------
# Describing a result without a model
# ---------------------------------------------------------------------------

def _fmt(value, unit: str) -> str:
    from src.viz.money import fmt as money_fmt

    if value is None or pd.isna(value):
        return "n/a"
    if unit == "currency":
        return money_fmt(value)
    if unit == "percent":
        return f"{value:,.1f}%"
    if unit == "score":
        return f"{value:,.2f}/5"
    if unit == "days":
        return f"{value:,.1f} days"
    return f"{value:,.0f}"


def _describe_without_model(plan, df: pd.DataFrame) -> str:
    """A factual summary of the result, written from the plan rather than by a model.

    Deliberately states only what the numbers say. No causal language, no
    speculation — an unaided template cannot know why something is true, and
    should not imply that it does.
    """
    metric_col = plan.metric.key
    unit = plan.metric.unit

    if plan.dimension is None:
        value = df.iloc[0][metric_col]
        line = f"**{_fmt(value, unit)}** — {plan.metric.label.lower()}"
        if plan.region:
            line += f" in {plan.region}"
        if plan.year:
            line += f" during {plan.year}"
        if "orders" in df.columns:
            line += f", across {int(df.iloc[0]['orders']):,} orders"
        return line + "."

    dim_col = plan.dimension.key
    top = df.iloc[0]
    # "leads with" is wrong when the question asked for the worst — the first row
    # is then the bottom of the ranking, not the top of it.
    lead_verb = "is lowest at" if plan.ascending else "leads with"
    tail_verb = "the highest shown is" if plan.ascending else "the lowest shown is"
    parts = [f"**{top[dim_col]}** {lead_verb} **{_fmt(top[metric_col], unit)}**"]
    if len(df) > 1:
        last = df.iloc[-1]
        parts.append(f"{tail_verb} {last[dim_col]} at {_fmt(last[metric_col], unit)}")

    scope = []
    if plan.region:
        scope.append(f"in {plan.region}")
    if plan.year:
        scope.append(f"during {plan.year}")
    scope_text = " " + " ".join(scope) if scope else ""

    summary = (f"{plan.metric.label} by {plan.dimension.label.lower()}{scope_text}: "
               + "; ".join(parts) + f". {len(df)} rows returned.")
    if plan.dimension.order_by_self:
        summary = (f"{plan.metric.label} by {plan.dimension.label.lower()}{scope_text}, "
                   f"{len(df)} periods. Range: "
                   f"{_fmt(df[metric_col].min(), unit)} to "
                   f"{_fmt(df[metric_col].max(), unit)}.")
    return summary


def _describe_dataframe(df: pd.DataFrame) -> str:
    return (f"{len(df):,} row(s) returned. The table below has the detail — "
            f"the model could not be reached to summarise it.")
