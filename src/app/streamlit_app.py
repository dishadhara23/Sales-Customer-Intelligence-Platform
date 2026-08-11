"""Streamlit front end for the "ask your data" agent.

    python -m streamlit run src/app/streamlit_app.py
    # or just double-click run_chatbot.command

Three backends, tried in order, all free except the last:

* **Ollama** — a model on this machine. No account, no key, works offline.
* **Claude** — the Anthropic API, if a key is present.
* **Query builder** — no model at all. Always works, so the app is never a
  dead end on a fresh clone.

Whichever answered is stated on screen. An answer from the deterministic
builder is never dressed up as a model's.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

from src.config import get_warehouse  # noqa: E402
from src.llm import query_builder  # noqa: E402
from src.llm.agent import AgentRun, DataAgent  # noqa: E402
from src.llm.providers import provider_status  # noqa: E402
from src.llm.schema_context import get_briefing  # noqa: E402
from src.viz import money  # noqa: E402
from src.viz.palette import (  # noqa: E402
    CATEGORICAL_LIGHT,
    SURFACE_LIGHT,
    TEXT_SECONDARY_LIGHT,
    plotly_layout,
)

st.set_page_config(page_title="Ask the Sales Warehouse",
                   page_icon="\N{BAR CHART}", layout="wide")

BACKEND_STYLE = {
    "ollama": ("#dff3e6", "#0b5c2e", "LOCAL MODEL"),
    "claude": ("#e8f1fd", "#14508f", "CLAUDE API"),
    "query_builder": ("#fdf0d9", "#7a5200", "QUERY BUILDER · no model"),
}

st.markdown(f"""
<style>
  .stApp {{ background: {SURFACE_LIGHT}; }}
  .kpi-row {{ display:flex; gap:12px; flex-wrap:wrap; margin-bottom:6px; }}
  .kpi {{ flex:1 1 150px; background:#f4f3f0; border-radius:10px;
          padding:12px 14px; border:1px solid #e6e4df; }}
  .kpi .label {{ font-size:11px; letter-spacing:.06em; text-transform:uppercase;
                 color:{TEXT_SECONDARY_LIGHT}; }}
  .kpi .value {{ font-size:24px; font-weight:650; color:#0b0b0b;
                 font-variant-numeric:tabular-nums; }}
  .pill {{ display:inline-block; padding:3px 10px; border-radius:999px;
           font-size:11px; font-weight:600; letter-spacing:.04em; }}
  .fx {{ font-size:11.5px; color:{TEXT_SECONDARY_LIGHT}; margin-top:2px; }}
</style>
""", unsafe_allow_html=True)


@st.cache_resource(show_spinner=False)
def _warehouse():
    return get_warehouse()


@st.cache_resource(show_spinner=False)
def _agent(_backend_key: str):
    return DataAgent(_warehouse())


@st.cache_data(show_spinner=False, ttl=30)
def _providers():
    return provider_status()


@st.cache_data(show_spinner=False)
def _headline(_url: str) -> dict:
    wh = _warehouse()
    row = pd.read_sql("""
        SELECT COUNT(*) AS orders,
               SUM(is_valid_sale) AS valid_orders,
               SUM(CASE WHEN is_valid_sale = 1 THEN gross_revenue ELSE 0 END) AS revenue,
               COUNT(DISTINCT customer_key) AS customers,
               AVG(review_score) AS review,
               AVG(days_to_deliver) AS days,
               MIN(order_date) AS first_date, MAX(order_date) AS last_date
        FROM fact_orders
    """, wh.engine).iloc[0]
    repeat = pd.read_sql(
        "SELECT 100.0 * SUM(is_repeat_customer) / COUNT(*) AS pct FROM dim_customer",
        wh.engine).iloc[0]["pct"]
    return {"orders": int(row["orders"]), "valid_orders": int(row["valid_orders"]),
            "revenue": float(row["revenue"]), "customers": int(row["customers"]),
            "review": float(row["review"]), "days": float(row["days"]),
            "repeat": float(repeat),
            "first_date": str(row["first_date"])[:10],
            "last_date": str(row["last_date"])[:10]}


def _auto_chart(df: pd.DataFrame, measure: str | None = None):
    """Chart only when the shape clearly supports one — a wrong chart is worse."""
    if df.empty or len(df) > 40 or len(df.columns) < 2:
        return None
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    labels = [c for c in df.columns if c not in numeric]
    if not numeric or not labels:
        return None
    label = labels[0]
    if df[label].nunique() != len(df):
        return None

    # The headline measure is conventionally the last selected column; the first
    # is usually a supporting count.
    value = measure or numeric[-1]
    is_money = any(k in value.lower() for k in ("revenue", "value", "freight", "sales"))
    plot = df.copy()
    if is_money:
        plot[value] = plot[value].map(lambda v: money.active().convert(v))

    ordered = plot.sort_values(value, ascending=True)
    fig = px.bar(ordered, x=value, y=label, orientation="h",
                 text=ordered[value].map(
                     lambda v: f"{money.symbol()}{v:,.0f}" if is_money
                     else (f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}")))
    fig.update_traces(marker_color=CATEGORICAL_LIGHT[0], marker_line_width=0,
                      textposition="outside", textfont_size=11, cliponaxis=False)
    fig.update_layout(**plotly_layout(), height=max(240, 26 * len(df) + 90),
                      showlegend=False, bargap=0.35)
    axis_title = value.replace("_", " ")
    fig.update_xaxes(title=f"{axis_title} ({money.active().code})" if is_money else axis_title)
    fig.update_yaxes(title=None)
    return fig


def _render_run(run: AgentRun) -> None:
    bg, fg, text = BACKEND_STYLE.get(run.source, BACKEND_STYLE["query_builder"])
    st.markdown(
        f'<span class="pill" style="background:{bg};color:{fg}">answered by {text}</span>'
        f' &nbsp;<span class="fx">{run.seconds:.1f}s</span>',
        unsafe_allow_html=True)
    st.markdown(run.answer or "_No answer produced._")

    for i, step in enumerate(run.steps, start=1):
        icon = "\N{WHITE HEAVY CHECK MARK}" if step.ok else "\N{CROSS MARK}"
        label = step.purpose or f"Query {i}"
        summary = f"{icon}  {label} · {step.rows:,} rows · {step.seconds:.2f}s"
        with st.expander(summary, expanded=(i == len(run.steps) and step.ok)):
            st.code(step.sql, language="sql")
            if step.error:
                st.warning(step.error)
            if step.dataframe is not None and not step.dataframe.empty:
                st.dataframe(step.dataframe, use_container_width=True, height=260)

    df = run.last_dataframe
    if df is not None:
        numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
        # Default to the column the question was about. Falling back to the last
        # one charts "orders" for a question about review scores.
        default = (numeric.index(run.measure) if run.measure in numeric
                   else len(numeric) - 1)
        measure = None
        if len(numeric) > 1:
            measure = st.selectbox("Chart this measure", numeric,
                                   index=default, key=f"m{len(run.steps)}")
        fig = _auto_chart(df, measure)
        if fig is not None:
            st.plotly_chart(fig, use_container_width=True)


def main() -> None:
    wh = _warehouse()
    providers = _providers()
    active_backend = next((p["name"] for p in providers if p["available"]),
                          "query_builder")
    agent = _agent(active_backend)
    stats = _headline(wh.url)
    cur = money.active()

    st.title("Ask the Sales Warehouse")
    bg, fg, text = BACKEND_STYLE.get(agent.backend, BACKEND_STYLE["query_builder"])
    # "BACKEND:" prefix, because this pill states what is *available* while the
    # per-answer badge states what actually answered — and they can differ when
    # the model fails and the builder picks the question up.
    st.markdown(
        f'<span class="pill" style="background:{bg};color:{fg}">BACKEND: {text}</span> &nbsp; '
        f"Natural-language questions answered in SQL against a curated star schema. "
        f"Data covers {stats['first_date']} to {stats['last_date']}.",
        unsafe_allow_html=True)

    aov = stats["revenue"] / max(stats["valid_orders"], 1)
    st.markdown(f"""<div class="kpi-row">
      <div class="kpi"><div class="label">Gross revenue</div>
        <div class="value">{money.compact(stats['revenue'])}</div></div>
      <div class="kpi"><div class="label">Orders</div>
        <div class="value">{stats['orders']:,}</div></div>
      <div class="kpi"><div class="label">Customers</div>
        <div class="value">{stats['customers']:,}</div></div>
      <div class="kpi"><div class="label">Avg order value</div>
        <div class="value">{money.fmt(aov, 2)}</div></div>
      <div class="kpi"><div class="label">Repeat rate</div>
        <div class="value">{stats['repeat']:.1f}%</div></div>
      <div class="kpi"><div class="label">Avg review</div>
        <div class="value">{stats['review']:.2f}/5</div></div>
      <div class="kpi"><div class="label">Avg delivery</div>
        <div class="value">{stats['days']:.1f} d</div></div>
    </div>""", unsafe_allow_html=True)
    if cur.is_converted:
        st.markdown(f'<div class="fx">Amounts shown in {cur.code} — {cur.note}.</div>',
                    unsafe_allow_html=True)

    # ---- sidebar ---------------------------------------------------------
    with st.sidebar:
        st.subheader("Backend")
        for p in providers:
            mark = "\N{WHITE HEAVY CHECK MARK}" if p["available"] else "\N{HEAVY MINUS SIGN}"
            st.markdown(f"{mark} **{p['label']}**  \n"
                        f"<span class='fx'>{p['detail']} · {p['cost']}</span>",
                        unsafe_allow_html=True)
        st.markdown(f"\N{WHITE HEAVY CHECK MARK} **Query builder**  \n"
                    f"<span class='fx'>always available · no model, no cost</span>",
                    unsafe_allow_html=True)

        if agent.backend == "query_builder":
            st.info("No model running, so questions are answered by the built-in "
                    "query builder. It handles a wide range of questions but "
                    "matches vocabulary rather than understanding sentences.\n\n"
                    "For free-form questions, install Ollama — see "
                    "`docs/CHATBOT_SETUP.md`.")

        st.subheader("Connection")
        st.write(f"Warehouse: `{wh.dialect}`")
        st.write(f"Currency: `{cur.code}`")

        st.subheader("Tables in scope")
        st.caption("Staging tables are hidden from the model, so it cannot reach "
                   "for the un-modelled keys.")
        for table in get_briefing(wh).tables:
            st.write(f"`{table}`")

    # ---- question --------------------------------------------------------
    st.subheader("Try one of these")
    examples = (query_builder.SUGGESTIONS[:6] if agent.backend == "query_builder"
                else ["Which product categories have the worst review scores?",
                      "What is the average delivery time by region?",
                      "How much revenue came from credit card orders in 2018?",
                      "Which states have the slowest delivery?",
                      "How many customers ordered more than once?",
                      "Show monthly revenue for 2018"])
    cols = st.columns(2)
    for i, question in enumerate(examples):
        if cols[i % 2].button(question, use_container_width=True, key=f"ex{i}"):
            st.session_state["pending"] = question

    typed = st.chat_input("Ask a question about sales, customers, delivery or payments…")
    if typed:
        st.session_state["pending"] = typed

    question = st.session_state.pop("pending", None)
    if not question:
        return

    st.markdown("---")
    st.markdown(f"**Q:** {question}")
    spinner = ("Thinking locally…" if agent.backend == "ollama"
               else "Querying the warehouse…")
    with st.spinner(spinner):
        run = agent.ask(question)
    _render_run(run)


if __name__ == "__main__":
    main()
