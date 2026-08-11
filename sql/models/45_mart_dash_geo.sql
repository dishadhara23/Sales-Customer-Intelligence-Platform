-- model: mart_dash_geo
-- materialized: table
-- depends_on: fact_orders, mart_geo_performance
-- description: State-level measures by month, so the map responds to the year
--   filter. Coordinates come from mart_geo_performance rather than being
--   recomputed, keeping one definition of "where a state is".

WITH monthly AS (
    SELECT
        {{ year_month(f.order_date) }}                              AS year_month,
        f.customer_state                                            AS state_code,
        COALESCE(f.customer_region, 'Unknown')                      AS region,
        COUNT(*)                                                    AS orders,
        SUM(f.is_valid_sale)                                        AS valid_orders,
        {{ round2( SUM(CASE WHEN f.is_valid_sale = 1 THEN f.gross_revenue ELSE 0 END) ) }}
                                                                    AS revenue,
        {{ round2( SUM(CASE WHEN f.is_valid_sale = 1 THEN f.freight_revenue ELSE 0 END) ) }}
                                                                    AS freight_revenue,
        {{ round2( SUM(COALESCE(f.review_score, 0)) ) }}            AS review_score_sum,
        SUM(CASE WHEN f.review_score IS NOT NULL THEN 1 ELSE 0 END) AS review_count,
        {{ round2( SUM(COALESCE(f.days_to_deliver, 0)) ) }}         AS deliver_days_sum,
        SUM(CASE WHEN f.days_to_deliver IS NOT NULL THEN 1 ELSE 0 END) AS deliver_count,
        SUM(COALESCE(f.is_late_delivery, 0))                        AS late_orders
    FROM fact_orders f
    WHERE f.customer_state IS NOT NULL
    GROUP BY {{ year_month(f.order_date) }}, f.customer_state, f.customer_region
)

SELECT
    m.year_month,
    m.state_code,
    m.region,
    g.latitude,
    g.longitude,
    m.orders,
    m.valid_orders,
    m.revenue,
    m.freight_revenue,
    m.review_score_sum,
    m.review_count,
    m.deliver_days_sum,
    m.deliver_count,
    m.late_orders
FROM monthly m
LEFT JOIN mart_geo_performance g ON g.state_code = m.state_code
