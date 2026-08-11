-- model: mart_dash_delivery
-- materialized: table
-- depends_on: fact_orders
-- description: Delivery-speed bucket by month and region (plus 'ALL'), holding
--   sums rather than averages so the browser can recombine months and still
--   compute a correctly weighted review score.

WITH base AS (
    SELECT
        {{ year_month(order_date) }}                AS year_month,
        COALESCE(customer_region, 'Unknown')        AS region,
        CASE
            WHEN days_to_deliver IS NULL THEN 'not delivered'
            WHEN days_to_deliver <=  3   THEN '0-3 days'
            WHEN days_to_deliver <=  7   THEN '4-7 days'
            WHEN days_to_deliver <= 14   THEN '8-14 days'
            WHEN days_to_deliver <= 30   THEN '15-30 days'
            ELSE '30+ days'
        END                                         AS bucket,
        CASE
            WHEN days_to_deliver IS NULL THEN 6
            WHEN days_to_deliver <=  3   THEN 1
            WHEN days_to_deliver <=  7   THEN 2
            WHEN days_to_deliver <= 14   THEN 3
            WHEN days_to_deliver <= 30   THEN 4
            ELSE 5
        END                                         AS bucket_order,
        review_score,
        gross_revenue,
        days_to_deliver
    FROM fact_orders
),

scoped AS (
    SELECT * FROM base
    UNION ALL
    SELECT year_month, 'ALL', bucket, bucket_order, review_score, gross_revenue,
           days_to_deliver
    FROM base
)

SELECT
    year_month,
    region,
    bucket,
    bucket_order,
    COUNT(*)                                                    AS orders,
    {{ round2( SUM(gross_revenue) ) }}                          AS revenue,
    {{ round2( SUM(COALESCE(review_score, 0)) ) }}              AS review_score_sum,
    SUM(CASE WHEN review_score IS NOT NULL THEN 1 ELSE 0 END)   AS review_count,
    SUM(CASE WHEN review_score <= 2 THEN 1 ELSE 0 END)          AS detractor_orders,
    SUM(CASE WHEN review_score >= 4 THEN 1 ELSE 0 END)          AS promoter_orders,
    {{ round2( SUM(COALESCE(days_to_deliver, 0)) ) }}           AS deliver_days_sum,
    SUM(CASE WHEN days_to_deliver IS NOT NULL THEN 1 ELSE 0 END) AS deliver_count
FROM scoped
GROUP BY year_month, region, bucket, bucket_order
