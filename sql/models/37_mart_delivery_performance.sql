-- model: mart_delivery_performance
-- materialized: table
-- depends_on: fact_orders
-- description: Delivery-speed cohorts crossed with review outcome. This is the
--   causal bridge the dashboard needs: it turns "our rating dropped" into
--   "our rating dropped because the >30-day delivery bucket grew", which is
--   an operations problem with an owner rather than a vague CX problem.

WITH bucketed AS (
    SELECT
        {{ month_start(order_date) }}                       AS month_start_date,
        customer_region                                     AS region,
        CASE
            WHEN days_to_deliver IS NULL     THEN 'not delivered'
            WHEN days_to_deliver <=  3       THEN '0-3 days'
            WHEN days_to_deliver <=  7       THEN '4-7 days'
            WHEN days_to_deliver <= 14       THEN '8-14 days'
            WHEN days_to_deliver <= 30       THEN '15-30 days'
            ELSE '30+ days'
        END                                                 AS delivery_speed_bucket,
        CASE
            WHEN days_to_deliver IS NULL     THEN 99
            WHEN days_to_deliver <=  3       THEN 1
            WHEN days_to_deliver <=  7       THEN 2
            WHEN days_to_deliver <= 14       THEN 3
            WHEN days_to_deliver <= 30       THEN 4
            ELSE 5
        END                                                 AS bucket_order,
        review_score,
        gross_revenue,
        is_late_delivery,
        delivery_vs_estimate_days,
        days_to_deliver
    FROM fact_orders
)

SELECT
    month_start_date,
    {{ year_month(month_start_date) }}                                  AS year_month,
    region,
    delivery_speed_bucket,
    bucket_order,
    COUNT(*)                                                            AS orders,
    {{ round2( SUM(gross_revenue) ) }}                                  AS revenue,
    {{ round2( AVG(review_score) ) }}                                   AS avg_review_score,
    SUM(CASE WHEN review_score <= 2 THEN 1 ELSE 0 END)                  AS detractor_orders,
    SUM(CASE WHEN review_score >= 4 THEN 1 ELSE 0 END)                  AS promoter_orders,
    {{ round2( 100.0 * SUM(CASE WHEN review_score <= 2 THEN 1 ELSE 0 END)
               / NULLIF(SUM(CASE WHEN review_score IS NOT NULL THEN 1 ELSE 0 END), 0) ) }}
                                                                        AS detractor_rate_pct,
    {{ round2( AVG(days_to_deliver) ) }}                                AS avg_days_to_deliver,
    {{ round2( AVG(delivery_vs_estimate_days) ) }}                      AS avg_days_vs_estimate,
    {{ round2( 100.0 * SUM(COALESCE(is_late_delivery, 0))
               / NULLIF(SUM(CASE WHEN is_late_delivery IS NOT NULL THEN 1 ELSE 0 END), 0) ) }}
                                                                        AS late_delivery_rate_pct
FROM bucketed
GROUP BY month_start_date, region, delivery_speed_bucket, bucket_order
