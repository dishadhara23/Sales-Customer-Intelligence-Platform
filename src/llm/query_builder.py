"""Deterministic natural-language → SQL, with no model involved.

This is the floor the app never falls below. It runs on a fresh clone with no
API key, no download and no account, and it answers a wide range of real
questions rather than matching a fixed list.

How it works
------------
Three passes over the question, each independent:

1. **Metric** — what number is being asked for (revenue, orders, review score,
   delivery days, …). Chosen by keyword, defaulting to revenue.
2. **Dimension** — what to break it down by (region, state, category, month,
   payment type, …). Optional; without one you get a single total.
3. **Modifiers** — filters and shaping: a year, a region, a top-N, a sort
   direction, a "delivered only" restriction.

Those three choices index into a table of known-good SQL fragments, so the
generated query is always valid and always uses the correct grain. It cannot
invent a column or fabricate a join.

What it deliberately does not do
--------------------------------
It has no idea what a sentence *means*. It matches vocabulary. Ask something
outside its vocabulary and it says so plainly rather than guessing — a wrong
answer delivered confidently is worse than an honest "I can't answer that".
That honesty is also what makes it a sane fallback: you always know whether you
got a real answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    expr: str                 # SQL aggregate over fact_orders
    unit: str                 # "currency" | "count" | "score" | "days" | "percent"
    keywords: tuple[str, ...]
    higher_is_better: bool = True


METRICS: tuple[Metric, ...] = (
    Metric("revenue", "Revenue",
           "SUM(CASE WHEN f.is_valid_sale = 1 THEN f.gross_revenue ELSE 0 END)",
           "currency",
           ("revenue", "sales", "turnover", "income", "money", "earned", "gmv",
            "made", "worth", "value")),
    Metric("orders", "Orders",
           "SUM(f.is_valid_sale)", "count",
           ("orders", "order", "purchases", "transactions", "sold", "volume",
            "how many orders")),
    Metric("customers", "Customers",
           "COUNT(DISTINCT f.customer_key)", "count",
           ("customers", "customer", "buyers", "people", "shoppers", "users")),
    Metric("aov", "Average order value",
           "SUM(CASE WHEN f.is_valid_sale = 1 THEN f.gross_revenue ELSE 0 END) "
           "/ NULLIF(SUM(f.is_valid_sale), 0)", "currency",
           ("average order", "aov", "basket", "order value", "spend per order",
            "average spend", "average sale")),
    Metric("review", "Average review score",
           "AVG(f.review_score)", "score",
           ("review", "rating", "score", "satisfaction", "stars", "happy",
            "unhappy", "complaint")),
    Metric("delivery_days", "Average days to deliver",
           "AVG(f.days_to_deliver)", "days",
           ("delivery time", "days to deliver", "how long", "shipping time",
            "lead time", "took to arrive", "delivery", "deliver", "slow",
            "slowest", "slower", "fast", "fastest", "quick", "quickest"),
           higher_is_better=False),
    Metric("late_pct", "Late delivery rate",
           "100.0 * SUM(COALESCE(f.is_late_delivery, 0)) "
           "/ NULLIF(SUM(CASE WHEN f.is_late_delivery IS NOT NULL THEN 1 ELSE 0 END), 0)",
           "percent",
           ("late delivery rate", "late delivery", "late deliveries", "on time rate",
            "late", "delayed", "overdue", "missed", "on time", "punctual"),
           higher_is_better=False),
    Metric("freight", "Freight cost",
           "SUM(CASE WHEN f.is_valid_sale = 1 THEN f.freight_revenue ELSE 0 END)",
           "currency",
           ("freight", "shipping cost", "delivery cost", "postage")),
    Metric("units", "Units sold",
           "SUM(CASE WHEN f.is_valid_sale = 1 THEN f.item_count ELSE 0 END)",
           "count", ("units", "items", "products sold", "quantity")),
    Metric("cancel_pct", "Cancellation rate",
           "100.0 * SUM(f.is_canceled) / NULLIF(COUNT(*), 0)", "percent",
           ("cancel", "cancelled", "canceled", "refund"),
           higher_is_better=False),
)


@dataclass(frozen=True)
class Dimension:
    key: str
    label: str
    expr: str
    keywords: tuple[str, ...]
    needs_items: bool = False   # requires the line-item grain
    order_by_self: bool = False  # chronological rather than by metric


DIMENSIONS: tuple[Dimension, ...] = (
    Dimension("region", "Region", "f.customer_region",
              ("region", "regions", "area", "part of brazil", "north", "south",
               "southeast", "northeast", "central-west")),
    Dimension("state", "State", "f.customer_state",
              ("state", "states", "where", "location", "geography", "province")),
    Dimension("city", "City", "f.customer_city",
              ("city", "cities", "town")),
    Dimension("month", "Month",
              "{{ year_month(f.order_date) }}",
              ("month", "monthly", "over time", "trend", "by month", "each month",
               "month by month", "timeline"),
              order_by_self=True),
    Dimension("year", "Year", "CAST({{ year(f.order_date) }} AS TEXT)",
              ("year", "yearly", "annual", "by year", "each year"),
              order_by_self=True),
    Dimension("payment", "Payment type", "f.primary_payment_type",
              ("payment", "pay", "card", "boleto", "voucher", "instalment",
               "installment")),
    Dimension("status", "Order status", "f.order_status",
              ("status", "state of order", "stage")),
    Dimension("category", "Category", "oi.category",
              ("category", "categories", "product type", "product",
               "what sells", "which products"),
              needs_items=True),
    Dimension("seller_state", "Seller state", "oi.seller_state",
              ("seller", "sellers", "vendor", "merchant"), needs_items=True),
    Dimension("weekday", "Day of week", "d.day_name",
              ("day of week", "weekday", "weekend", "which day")),
    Dimension("delivery_band", "Delivery speed",
              """CASE
            WHEN f.days_to_deliver IS NULL THEN 'not delivered'
            WHEN f.days_to_deliver <=  3   THEN '0-3 days'
            WHEN f.days_to_deliver <=  7   THEN '4-7 days'
            WHEN f.days_to_deliver <= 14   THEN '8-14 days'
            WHEN f.days_to_deliver <= 30   THEN '15-30 days'
            ELSE '30+ days' END""",
              ("delivery band", "delivery speed", "how fast", "speed bucket",
               "delivery bucket")),
)

REGION_WORDS = {
    "southeast": "Southeast", "south east": "Southeast",
    "northeast": "Northeast", "north east": "Northeast",
    "south": "South", "north": "North",
    "central-west": "Central-West", "central west": "Central-West",
    "midwest": "Central-West",
}

STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are",
    "was", "were", "do", "does", "did", "how", "what", "which", "who", "why",
    "much", "many", "most", "we", "our", "us", "they", "them", "it", "that",
    "this", "there", "by", "at", "from", "with", "about", "over", "show", "me",
    "give", "list", "tell", "get", "per", "each", "top", "best", "worst",
}


@dataclass
class Plan:
    metric: Metric
    dimension: Dimension | None
    year: int | None = None
    region: str | None = None
    limit: int | None = None
    ascending: bool = False
    delivered_only: bool = False
    notes: list[str] = field(default_factory=list)

    def describe(self) -> str:
        bits = [self.metric.label.lower()]
        if self.dimension:
            bits.append(f"by {self.dimension.label.lower()}")
        if self.region:
            bits.append(f"in {self.region}")
        if self.year:
            bits.append(f"during {self.year}")
        if self.limit:
            bits.append(f"({'bottom' if self.ascending else 'top'} {self.limit})")
        return " ".join(bits).capitalize()


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def _score(question: str, keywords: tuple[str, ...]) -> int:
    """Longest-phrase-wins keyword match, so 'order value' beats 'order'."""
    q = f" {question} "
    best = 0
    for kw in keywords:
        if f" {kw} " in q or q.strip().endswith(" " + kw) or kw in q:
            best = max(best, len(kw.split()) * 10 + len(kw))
    return best


def _matched_phrase(question: str, keywords: tuple[str, ...]) -> str | None:
    """The longest keyword phrase present, or None."""
    hits = [kw for kw in keywords if kw in f" {question} "]
    return max(hits, key=len) if hits else None


def parse(question: str) -> Plan | None:
    q = re.sub(r"[^\w\s%-]", " ", question.lower())
    q = re.sub(r"\s+", " ", q).strip()
    if not q:
        return None

    plan_year: int | None = None
    plan_region: str | None = None

    # --- 1. Pull out filters first, and remove them from the text ----------
    # Otherwise "in the south" both filters to South *and* looks like a request
    # to break down by region, and you get every region back instead of one.
    if m := re.search(r"\b(201[6-8])\b", q):
        # An int, not the raw text: this value is interpolated into SQL, and a
        # parsed integer cannot carry anything but digits.
        plan_year = int(m.group(1))
        q = q.replace(m.group(1), " ")

    for word, canonical in sorted(REGION_WORDS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b(?:in|for|from|across)\s+(?:the\s+)?{re.escape(word)}\b", q):
            plan_region = canonical
            q = re.sub(rf"\b(?:in|for|from|across)\s+(?:the\s+)?{re.escape(word)}\b",
                       " ", q)
            break

    # --- 2. Dimension next, and remove its phrase too ----------------------
    # "average review score by delivery speed" must not read "delivery speed" as
    # the *measure*; the dimension claims that phrase first.
    dimension = max(DIMENSIONS, key=lambda d: _score(q, d.keywords))
    if _score(q, dimension.keywords) == 0:
        dimension = None
    else:
        phrase = _matched_phrase(q, dimension.keywords)
        if phrase:
            q = q.replace(phrase, " ")

    # --- 3. Metric from whatever vocabulary is left ------------------------
    metric = max(METRICS, key=lambda m: _score(q, m.keywords))
    if _score(q, metric.keywords) == 0:
        # No measure named. Revenue is a fair default only when a breakdown was
        # asked for; with neither, we have not understood the question at all.
        if dimension is None:
            return None
        metric = next(m for m in METRICS if m.key == "revenue")

    plan = Plan(metric=metric, dimension=dimension)
    plan.year = plan_year
    plan.region = plan_region

    if m := re.search(r"\btop\s+(\d{1,3})\b", q):
        plan.limit = min(int(m.group(1)), 100)
    elif m := re.search(r"\b(\d{1,3})\s+(?:best|biggest|largest|worst)\b", q):
        plan.limit = min(int(m.group(1)), 100)
    elif dimension and dimension.key in {"state", "city", "category", "seller_state"}:
        plan.limit = 15

    # Work out what the reader wants ranked first, then translate that into a
    # sort direction using the metric's polarity. "Slowest delivery" wants the
    # HIGHEST day count; "worst revenue" wants the lowest. Reading the words as
    # a direction directly gets one of those two backwards.
    wants_worst = any(w in q for w in
                      ("worst", "lowest", "least", "bottom", "slowest", "slower",
                       "poorest", "weakest", "longest"))
    wants_best = any(w in q for w in
                     ("best", "highest", "most", "top", "largest", "fastest",
                      "quickest", "shortest", "biggest"))
    if wants_worst:
        # Worst = the bad end of the scale.
        plan.ascending = metric.higher_is_better
    elif wants_best:
        plan.ascending = not metric.higher_is_better
    else:
        # No preference stated: lead with the largest magnitude either way.
        plan.ascending = False

    if metric.key in {"delivery_days", "late_pct"} or (
            dimension and dimension.key == "delivery_band"):
        plan.delivered_only = metric.key == "delivery_days"

    return plan


# --------------------------------------------------------------------------
# SQL generation
# --------------------------------------------------------------------------


def to_sql(plan: Plan, row_limit: int = 1000) -> str:
    needs_items = plan.dimension is not None and plan.dimension.needs_items
    needs_date = plan.dimension is not None and plan.dimension.key == "weekday"

    if needs_items:
        # At the line-item grain the metric must be re-expressed, or every
        # order-level number would be multiplied by its item count.
        metric_expr = {
            "revenue": "SUM(oi.item_gross_value)",
            "freight": "SUM(oi.freight_value)",
            "units": "COUNT(*)",
            "orders": "COUNT(DISTINCT oi.order_id)",
            "customers": "COUNT(DISTINCT oi.customer_key)",
            "aov": "SUM(oi.item_gross_value) / NULLIF(COUNT(DISTINCT oi.order_id), 0)",
        }.get(plan.metric.key)
        if metric_expr is None:
            metric_expr = plan.metric.expr  # review/delivery come from fact_orders
    else:
        metric_expr = plan.metric.expr

    select_parts, group_parts, order_by = [], [], None
    if plan.dimension:
        select_parts.append(f"{plan.dimension.expr} AS {plan.dimension.key}")
        group_parts.append(plan.dimension.expr)
    select_parts.append(f"{{{{ round2({metric_expr}) }}}} AS {plan.metric.key}")

    # A supporting count makes every answer auditable — a headline built on
    # 4 orders should never look the same as one built on 40,000.
    if plan.metric.key not in {"orders", "units"}:
        select_parts.append(
            "COUNT(DISTINCT oi.order_id) AS orders" if needs_items
            else "SUM(f.is_valid_sale) AS orders")

    if needs_items:
        frm = ["FROM fact_order_items oi",
               "JOIN fact_orders f ON f.order_id = oi.order_id"]
    else:
        frm = ["FROM fact_orders f"]
    if needs_date:
        frm.append("JOIN dim_date d ON d.date_key = f.date_key")

    where = []
    if plan.year:
        where.append(f"{{{{ year(f.order_date) }}}} = {plan.year}")
    if plan.region:
        where.append(f"f.customer_region = '{plan.region}'")
    if plan.delivered_only:
        where.append("f.days_to_deliver IS NOT NULL")
    if plan.metric.key == "review":
        where.append("f.review_score IS NOT NULL")
    if needs_items:
        where.append("oi.order_status NOT IN ('canceled', 'unavailable')")

    if plan.dimension:
        if plan.dimension.order_by_self:
            order_by = f"ORDER BY {plan.dimension.key}"
        else:
            order_by = f"ORDER BY {plan.metric.key} {'ASC' if plan.ascending else 'DESC'}"

    sql = ["SELECT " + ",\n       ".join(select_parts)]
    sql += frm
    if where:
        sql.append("WHERE " + "\n  AND ".join(where))
    if group_parts:
        sql.append("GROUP BY " + ", ".join(group_parts))
        # Exclude groups too small to mean anything, but only where a
        # distribution is being ranked — never on a time series.
        if not plan.dimension.order_by_self:
            sql.append("HAVING COUNT(*) >= 20")
    if order_by:
        sql.append(order_by)
    sql.append(f"LIMIT {min(plan.limit or row_limit, row_limit)}")
    return "\n".join(sql)


SUGGESTIONS = [
    "Revenue by region in 2018",
    "Top 10 categories by revenue",
    "Average review score by delivery speed",
    "Which states have the slowest delivery?",
    "Monthly orders over time",
    "Cancellation rate by state",
    "Average order value by payment type",
    "Customers by region",
    "Freight cost by category",
    "Late delivery rate by region in 2018",
]


def explain_failure(question: str) -> str:
    """Say plainly what could not be understood, and what would work."""
    known_metrics = ", ".join(sorted({m.label.lower() for m in METRICS}))
    known_dims = ", ".join(sorted({d.label.lower() for d in DIMENSIONS}))
    return (
        f"I couldn't turn that into a query.\n\n"
        f"This is the built-in query builder, not a language model — it matches "
        f"vocabulary rather than understanding sentences, and it would rather say "
        f"so than guess.\n\n"
        f"**Measures it knows:** {known_metrics}.\n\n"
        f"**Breakdowns it knows:** {known_dims}.\n\n"
        f"You can also add a year (2016–2018), a region, or a \"top N\".\n\n"
        f"For questions in plain English, install Ollama — see the sidebar."
    )
