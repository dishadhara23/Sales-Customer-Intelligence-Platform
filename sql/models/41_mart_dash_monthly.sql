-- model: mart_dash_monthly
-- materialized: table
-- depends_on: fact_orders
-- description: Monthly measures split by region, plus an 'ALL' region rollup.
--   Every measure here is additive, so the browser can safely re-aggregate
--   across months when the year filter changes. Non-additive measures
--   (distinct customers) live in mart_dash_kpi instead.

WITH base AS (
    SELECT
        {{ year_month(order_date) }}                        AS year_month,
        {{ month_start(order_date) }}                       AS month_start_date,
        COALESCE(customer_region, 'Unknown')                AS region,
        is_valid_sale, is_canceled, is_delivered, is_late_delivery,
        gross_revenue, freight_revenue, item_count, review_score, days_to_deliver,
        CASE WHEN cohort_month = {{ month_start(order_date) }} THEN 1 ELSE 0 END AS is_new_customer_order
    FROM fact_orders
),

scoped AS (
    SELECT year_month, month_start_date, region, is_valid_sale, is_canceled,
           is_delivered, is_late_delivery, gross_revenue, freight_revenue,
           item_count, review_score, days_to_deliver, is_new_customer_order
    FROM base
    UNION ALL
    SELECT year_month, month_start_date, 'ALL', is_valid_sale, is_canceled,
           is_delivered, is_late_delivery, gross_revenue, freight_revenue,
           item_count, review_score, days_to_deliver, is_new_customer_order
    FROM base
)

SELECT
    year_month,
    month_start_date,
    region,
    COUNT(*)                                                        AS orders,
    SUM(is_valid_sale)                                              AS valid_orders,
    SUM(is_canceled)                                                AS canceled_orders,
    SUM(is_new_customer_order)                                      AS new_customer_orders,
    {{ round2( SUM(CASE WHEN is_valid_sale = 1 THEN gross_revenue ELSE 0 END) ) }}
                                                                    AS revenue,
    {{ round2( SUM(CASE WHEN is_valid_sale = 1 THEN freight_revenue ELSE 0 END) ) }}
                                                                    AS freight_revenue,
    SUM(CASE WHEN is_valid_sale = 1 THEN item_count ELSE 0 END)     AS units,
    -- Sums, not averages: an average of averages is wrong once the browser
    -- combines months. The front end divides these when it renders.
    {{ round2( SUM(COALESCE(review_score, 0)) ) }}                  AS review_score_sum,
    SUM(CASE WHEN review_score IS NOT NULL THEN 1 ELSE 0 END)       AS review_count,
    {{ round2( SUM(COALESCE(days_to_deliver, 0)) ) }}               AS deliver_days_sum,
    SUM(CASE WHEN days_to_deliver IS NOT NULL THEN 1 ELSE 0 END)    AS deliver_count,
    SUM(COALESCE(is_late_delivery, 0))                              AS late_orders,
    SUM(is_delivered)                                               AS delivered_orders
FROM scoped
GROUP BY year_month, month_start_date, region
