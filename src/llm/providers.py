"""Pluggable back ends for turning a question into SQL.

Three tiers, tried in order, each a strict fallback for the one above:

1. **Ollama** — a model running on this machine. Free, private, offline, no
   account. The default.
2. **Claude** — the Anthropic API. Best quality, needs a key, costs money.
3. **Query builder** — no model at all (:mod:`src.llm.query_builder`). Always
   available, so the app is never a dead end on a fresh clone.

Why a two-call interface rather than tool use
---------------------------------------------
Every provider implements the same two methods: write SQL, then explain a
result. Tool-calling protocols differ between vendors and small local models
support them unevenly, whereas "produce SQL" and "describe this table" are
things every instruction-tuned model can do. Keeping the contract narrow is
what lets a 7B local model and a frontier API sit behind one interface.

Agentic behaviour has not been lost: :mod:`src.llm.agent` feeds SQL errors back
for a retry, which works with any provider precisely because the interface does
not depend on vendor-specific tool semantics.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")

# Where this project installs Ollama when the user has no system-wide copy.
LOCAL_OLLAMA = Path.home() / ".local" / "ollama" / "ollama"

SQL_SYSTEM = """\
You translate business questions into a single SQLite SELECT statement.

Rules, all of them absolute:
- Output ONLY the SQL. No prose, no explanation, no markdown fences.
- One SELECT (or WITH ... SELECT). Never INSERT/UPDATE/DELETE/DROP/CREATE.
- Use only the tables and columns listed in the schema below. Never invent one.
- Money is Brazilian Real. The data covers Sep 2016 to Oct 2018 only.
- Exclude cancelled orders with `is_valid_sale = 1` unless the question is
  specifically about cancellations.
- Count customers with `customer_key`, never with a per-order id.
- Always add a LIMIT (100 unless the question implies otherwise).
- Whatever you GROUP BY, also SELECT. A result of bare numbers with nothing
  naming each row cannot be read by anyone.

Where to look first. Every mart_* table is already aggregated at the grain in
its name, so the right one answers the question from a SINGLE table with NO
JOIN. Reach for a join only when no mart fits — a needless join is the most
common way these queries fail:
- by product category      -> mart_category_performance   (one row per category)
- by state, city or region -> mart_geo_performance        (one row per state)
- by month or over time    -> mart_kpi_monthly
- by day                   -> mart_kpi_daily
- delivery speed / lateness-> mart_delivery_performance
- payment type / instalments-> mart_payment_mix
- customer segments, RFM   -> mart_rfm, mart_customer_360
- repeat purchase, cohorts -> mart_cohort_retention
- funnel / drop-off        -> mart_order_funnel
- anything else, and all order-level filtering -> fact_orders

Column traps — getting these wrong produces a query that RUNS but is WRONG:
- "delivery time" / "how long to deliver" = `days_to_deliver`.
  NEVER `delivery_vs_estimate_days` (that is early/late vs the estimate and is
  negative for on-time orders).
- Customer counts use `customer_key`. Never `source_customer_id`.
- `retention_pct` is already 0-100. Do not multiply by 100.
- Sort delivery bands by `bucket_order`, funnel stages by `stage_order`.
"""

EXPLAIN_SYSTEM = """\
You state what a query result shows, for a non-technical business reader.

- Lead with the number or finding in one sentence.
- Then at most one more sentence, and ONLY about what the numbers themselves
  show: a comparison, a range, which row is largest or smallest, how many rows.

Quote figures; never calculate them. Every number you write must appear
verbatim in the result table. Do NOT add up a column, average it, work out a
difference, a share, a percentage or a growth rate. If the total is not a row in
the table, there is no total to report — say what the table shows instead. A
figure you computed yourself is indistinguishable from a made-up one, and it is
the single worst mistake you can make here.
- Never mention SQL, tables or columns. Never repeat the query.
- Money is Brazilian Real. ALWAYS write amounts as R$ followed by the number,
  exactly as they appear in the result — do NOT convert to any other currency
  (the application converts afterwards). Apply R$ ONLY to
  monetary columns (revenue, freight, order value). Review scores are "x.x out
  of 5", delivery times are "x.x days", rates are percentages, counts are plain
  numbers. Putting a currency symbol on a rating is a factual error.
- If the result is empty, say so plainly.

Do NOT speculate about causes. You are shown numbers, not reasons. Never write
"this suggests", "this indicates", "likely because", "possibly due to", or any
explanation of WHY a number is what it is — you cannot know that from a table,
and inventing it is worse than saying less. Describe, do not interpret.

A small row count is a caveat worth stating: if a group has under ~50 rows, say
the sample is small.
"""


def strip_sql(text: str) -> str:
    """Pull a bare SQL statement out of whatever the model wrapped it in."""
    text = text.strip()
    if "```" in text:
        blocks = re.findall(r"```(?:sql)?\s*(.*?)```", text, re.S | re.I)
        if blocks:
            text = blocks[0]
    # Drop any lead-in ("Here's the SQL:", "Sure — try this:") by starting at the
    # first SQL keyword. Matching the keyword is reliable; enumerating the ways a
    # model might introduce itself is not.
    if start := re.search(r"\b(SELECT|WITH)\b", text, re.I):
        text = text[start.start():]
    # Models sometimes add a trailing note after the statement.
    if ";" in text:
        text = text[: text.index(";") + 1]
    return text.strip().rstrip(";").strip()


class Provider(ABC):
    name: str
    label: str
    cost_note: str

    @abstractmethod
    def available(self) -> bool: ...

    @abstractmethod
    def write_sql(self, question: str, schema: str, hint: str | None = None) -> str: ...

    @abstractmethod
    def explain(self, question: str, result_preview: str) -> str: ...


# ---------------------------------------------------------------------------
# Ollama — local, free
# ---------------------------------------------------------------------------


class OllamaProvider(Provider):
    name = "ollama"
    label = "Ollama (local)"
    cost_note = "free · runs on this machine · no account, no internet"

    def __init__(self, model: str = OLLAMA_MODEL, host: str = OLLAMA_HOST,
                 timeout: int = 180):
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout

    # -- server management --------------------------------------------------
    def _ping(self, timeout: float = 2.0) -> bool:
        try:
            with urllib.request.urlopen(f"{self.host}/api/version", timeout=timeout):
                return True
        except Exception:
            return False

    def _binary(self) -> str | None:
        return shutil.which("ollama") or (str(LOCAL_OLLAMA) if LOCAL_OLLAMA.exists() else None)

    def ensure_server(self) -> bool:
        """Start `ollama serve` if it isn't already running."""
        if self._ping():
            return True
        binary = self._binary()
        if binary is None:
            return False
        try:
            subprocess.Popen([binary, "serve"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        except OSError:
            return False
        for _ in range(20):          # server takes a moment to bind
            time.sleep(0.5)
            if self._ping():
                return True
        return False

    def models(self) -> list[str]:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=5) as r:
                return [m["name"] for m in json.load(r).get("models", [])]
        except Exception:
            return []

    def available(self) -> bool:
        if not self.ensure_server():
            return False
        installed = self.models()
        if not installed:
            return False
        # Accept the configured model, or fall back to any installed one so a
        # user who pulled a different model still gets a working app.
        if self.model in installed:
            return True
        preferred = [m for m in installed if "coder" in m or "sql" in m]
        self.model = (preferred or installed)[0]
        return True

    # -- generation ---------------------------------------------------------
    def _chat(self, system: str, user: str, max_tokens: int = 400) -> str:
        payload = json.dumps({
            "model": self.model,
            "stream": False,
            # Deterministic: the same question should give the same SQL, and
            # creativity is the last thing wanted from a query generator.
            "options": {"temperature": 0, "num_predict": max_tokens},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }).encode()
        req = urllib.request.Request(
            f"{self.host}/api/chat", data=payload,
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.load(r)["message"]["content"]

    def write_sql(self, question: str, schema: str, hint: str | None = None) -> str:
        user = f"SCHEMA\n======\n{schema}\n\nQUESTION\n========\n{question}"
        if hint:
            user += (f"\n\nYour previous attempt failed with:\n{hint}\n"
                     f"Return corrected SQL only.")
        return strip_sql(self._chat(SQL_SYSTEM, user))

    def explain(self, question: str, result_preview: str) -> str:
        from src.viz.money import convert_brl_text

        user = f"Question: {question}\n\nResult:\n{result_preview}"
        return convert_brl_text(self._chat(EXPLAIN_SYSTEM, user, max_tokens=300).strip())


# ---------------------------------------------------------------------------
# Claude — paid, best quality
# ---------------------------------------------------------------------------


class ClaudeProvider(Provider):
    name = "claude"
    label = "Claude (Anthropic API)"
    # Deliberately not quoting a figure: per-token prices change, and a stale
    # number stated confidently is worse than no number.
    cost_note = ("highest quality · needs an API key · paid per question "
                 "at Anthropic's current rates")

    def __init__(self, model: str | None = None, effort: str | None = None):
        from src.config import ANTHROPIC_EFFORT, ANTHROPIC_MODEL

        self.model = model or ANTHROPIC_MODEL
        self.effort = effort or ANTHROPIC_EFFORT
        self._client = None

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic()
        return self._client

    def available(self) -> bool:
        if not os.getenv("ANTHROPIC_API_KEY"):
            return False
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False
        return True

    def _message(self, system: str, user: str, max_tokens: int = 2000) -> str:
        response = self._get_client().messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system,
                     "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_config={"effort": self.effort},
        )
        # Check the stop reason before reading content: on a refusal the content
        # list can be empty and indexing it would raise.
        if response.stop_reason == "refusal":
            return "That request was declined by the model's safety system."
        return "".join(b.text for b in response.content if b.type == "text").strip()

    def write_sql(self, question: str, schema: str, hint: str | None = None) -> str:
        user = f"SCHEMA\n======\n{schema}\n\nQUESTION\n========\n{question}"
        if hint:
            user += f"\n\nYour previous attempt failed with:\n{hint}\nReturn corrected SQL only."
        return strip_sql(self._message(SQL_SYSTEM, user))

    def explain(self, question: str, result_preview: str) -> str:
        from src.viz.money import convert_brl_text

        return convert_brl_text(self._message(
            EXPLAIN_SYSTEM, f"Question: {question}\n\nResult:\n{result_preview}"))


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def get_provider(prefer: str | None = None) -> Provider | None:
    """Return the best available provider, or None to use the query builder.

    ``prefer`` (or ``LLM_PROVIDER``) forces a choice; otherwise Ollama is tried
    first because it is free, then Claude.
    """
    choice = (prefer or os.getenv("LLM_PROVIDER") or "auto").strip().lower()

    builders = {"ollama": OllamaProvider, "claude": ClaudeProvider}
    if choice in builders:
        provider = builders[choice]()
        return provider if provider.available() else None
    if choice in {"none", "builder", "off"}:
        return None

    for factory in (OllamaProvider, ClaudeProvider):
        provider = factory()
        try:
            if provider.available():
                return provider
        except Exception:
            continue
    return None


def provider_status() -> list[dict]:
    """Human-readable availability of every provider, for the UI sidebar."""
    rows = []
    for factory in (OllamaProvider, ClaudeProvider):
        provider = factory()
        try:
            ok = provider.available()
        except Exception:
            ok = False
        detail = ""
        if isinstance(provider, OllamaProvider):
            detail = provider.model if ok else "not running — see setup"
        elif isinstance(provider, ClaudeProvider):
            detail = provider.model if ok else "no ANTHROPIC_API_KEY"
        rows.append({"name": provider.name, "label": provider.label,
                     "available": ok, "detail": detail,
                     "cost": provider.cost_note})
    return rows
