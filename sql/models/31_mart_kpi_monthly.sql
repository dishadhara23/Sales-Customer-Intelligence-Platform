-- model: mart_kpi_monthly
-- materialized: table
-- depends_on: mart_kpi_daily, fact_orders
-- description: Monthly KPIs with month-over-month growth and a new-vs-returning
--   revenue split — the two numbers a commercial review actually opens with.

WITH monthly AS (
    SELECT
        {{ month_start(order_date) }}                                    AS month_start_date,
        {{ year_month(order_date) }}                                     AS year_month,
        COUNT(*)                                                         AS orders,
        SUM(is_valid_sale)                                               AS valid_orders,
        COUNT(DISTINCT customer_key)                                     AS active_customers,
        SUM(CASE WHEN is_valid_sale = 1 THEN gross_revenue ELSE 0 END)   AS revenue,
        SUM(CASE WHEN is_valid_sale = 1 THEN freight_revenue ELSE 0 END) AS freight_revenue,
        SUM(CASE WHEN is_valid_sale = 1 THEN item_count ELSE 0 END)      AS units_sold,
        SUM(is_canceled)                                                 AS canceled_orders,
        AVG(review_score)                                                AS avg_review_score,
        AVG(days_to_deliver)                                             AS avg_days_to_deliver,
        -- "New" = this order is the customer's first, judged on cohort month.
        SUM(CASE WHEN is_valid_sale = 1
                  AND cohort_month = {{ month_start(order_date) }}
                 THEN gross_revenue ELSE 0 END)                          AS new_customer_revenue,
        COUNT(DISTINCT CASE WHEN cohort_month = {{ month_start(order_date) }}
                            THEN customer_key END)                       AS new_customers
    FROM fact_orders
    GROUP BY {{ month_start(order_date) }}, {{ year_month(order_date) }}
)

SELECT
    month_start_date,
    year_month,
    orders,
    valid_orders,
    canceled_orders,
    active_customers,
    new_customers,
    active_customers - new_customers                                     AS returning_customers,
    units_sold,
    {{ round2(revenue) }}                                                AS revenue,
    {{ round2(freight_revenue) }}                                        AS freight_revenue,
    {{ round2(new_customer_revenue) }}                                   AS new_customer_revenue,
    {{ round2(revenue - new_customer_revenue) }}                         AS returning_customer_revenue,
    {{ round2( revenue / NULLIF(valid_orders, 0) ) }}                    AS avg_order_value,
    {{ round2( 1.0 * units_sold / NULLIF(valid_orders, 0) ) }}           AS avg_units_per_order,
    {{ round2( 100.0 * canceled_orders / NULLIF(orders, 0) ) }}          AS cancellation_rate_pct,
    {{ round2( 100.0 * (revenue - new_customer_revenue) / NULLIF(revenue, 0) ) }}
                                                                         AS returning_revenue_share_pct,
    {{ round2(avg_review_score) }}                                       AS avg_review_score,
    {{ round2(avg_days_to_deliver) }}                                    AS avg_days_to_deliver,
    {{ round2( LAG(revenue) OVER (ORDER BY month_start_date) ) }}        AS prev_month_revenue,
    {{ round2( 100.0 * (revenue - LAG(revenue) OVER (ORDER BY month_start_date))
               / NULLIF(LAG(revenue) OVER (ORDER BY month_start_date), 0) ) }}
                                                                         AS revenue_mom_pct
FROM monthly
