-- model: mart_dash_funnel
-- materialized: table
-- depends_on: fact_orders
-- description: Fulfilment funnel by month and region (plus 'ALL'), so the
--   funnel responds to the dashboard filters. Stage membership is measured on
--   milestone timestamps, matching mart_order_funnel.

WITH base AS (
    SELECT
        {{ year_month(order_date) }}                AS year_month,
        COALESCE(customer_region, 'Unknown')        AS region,
        order_approved_at,
        order_delivered_carrier_date,
        order_delivered_customer_date,
        is_late_delivery
    FROM fact_orders
),

scoped AS (
    SELECT * FROM base
    UNION ALL
    SELECT year_month, 'ALL', order_approved_at, order_delivered_carrier_date,
           order_delivered_customer_date, is_late_delivery
    FROM base
),

counted AS (
    SELECT
        year_month, region,
        COUNT(*)                                                                  AS s1,
        SUM(CASE WHEN order_approved_at IS NOT NULL THEN 1 ELSE 0 END)            AS s2,
        SUM(CASE WHEN order_delivered_carrier_date IS NOT NULL THEN 1 ELSE 0 END) AS s3,
        SUM(CASE WHEN order_delivered_customer_date IS NOT NULL THEN 1 ELSE 0 END) AS s4,
        SUM(CASE WHEN order_delivered_customer_date IS NOT NULL
                  AND COALESCE(is_late_delivery, 0) = 0 THEN 1 ELSE 0 END)        AS s5
    FROM scoped
    GROUP BY year_month, region
)

SELECT year_month, region, 1 AS stage_order, 'Purchased'             AS stage, s1 AS orders FROM counted
UNION ALL
SELECT year_month, region, 2, 'Payment approved',      s2 FROM counted
UNION ALL
SELECT year_month, region, 3, 'Handed to carrier',     s3 FROM counted
UNION ALL
SELECT year_month, region, 4, 'Delivered to customer', s4 FROM counted
UNION ALL
SELECT year_month, region, 5, 'Delivered on time',     s5 FROM counted
