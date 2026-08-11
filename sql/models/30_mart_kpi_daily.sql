-- model: mart_kpi_daily
-- materialized: table
-- depends_on: dim_date, fact_orders
-- description: Daily headline KPIs on a complete date spine (zero-order days
--   are present as zeros, not absent). Drives the trend tiles on the dashboard.

WITH daily AS (
    SELECT
        date_key,
        COUNT(*)                                                    AS orders,
        SUM(is_valid_sale)                                          AS valid_orders,
        SUM(is_canceled)                                            AS canceled_orders,
        SUM(is_delivered)                                           AS delivered_orders,
        COUNT(DISTINCT customer_key)                                AS active_customers,
        SUM(CASE WHEN is_valid_sale = 1 THEN gross_revenue ELSE 0 END)   AS revenue,
        SUM(CASE WHEN is_valid_sale = 1 THEN item_revenue ELSE 0 END)    AS item_revenue,
        SUM(CASE WHEN is_valid_sale = 1 THEN freight_revenue ELSE 0 END) AS freight_revenue,
        SUM(CASE WHEN is_valid_sale = 1 THEN item_count ELSE 0 END)      AS units_sold,
        AVG(review_score)                                           AS avg_review_score,
        AVG(days_to_deliver)                                        AS avg_days_to_deliver,
        SUM(COALESCE(is_late_delivery, 0))                          AS late_deliveries
    FROM fact_orders
    GROUP BY date_key
)

SELECT
    d.date_key,
    d.calendar_date,
    d.year_month,
    d.calendar_year,
    d.month_name,
    d.day_name,
    d.is_weekend,
    COALESCE(x.orders, 0)                                           AS orders,
    COALESCE(x.valid_orders, 0)                                     AS valid_orders,
    COALESCE(x.canceled_orders, 0)                                  AS canceled_orders,
    COALESCE(x.delivered_orders, 0)                                 AS delivered_orders,
    COALESCE(x.active_customers, 0)                                 AS active_customers,
    {{ round2( COALESCE(x.revenue, 0) ) }}                          AS revenue,
    {{ round2( COALESCE(x.item_revenue, 0) ) }}                     AS item_revenue,
    {{ round2( COALESCE(x.freight_revenue, 0) ) }}                  AS freight_revenue,
    COALESCE(x.units_sold, 0)                                       AS units_sold,
    {{ round2( COALESCE(x.revenue, 0) / NULLIF(x.valid_orders, 0) ) }}
                                                                    AS avg_order_value,
    {{ round2( 100.0 * x.canceled_orders / NULLIF(x.orders, 0) ) }}  AS cancellation_rate_pct,
    {{ round2( 100.0 * x.late_deliveries / NULLIF(x.delivered_orders, 0) ) }}
                                                                    AS late_delivery_rate_pct,
    {{ round2(x.avg_review_score) }}                                AS avg_review_score,
    {{ round2(x.avg_days_to_deliver) }}                             AS avg_days_to_deliver,
    -- 7-day trailing revenue smooths the very spiky Brazilian weekday pattern.
    {{ round2( AVG(COALESCE(x.revenue, 0)) OVER (
        ORDER BY d.date_key ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) ) }}
                                                                    AS revenue_7d_avg
FROM dim_date d
LEFT JOIN daily x ON x.date_key = d.date_key
