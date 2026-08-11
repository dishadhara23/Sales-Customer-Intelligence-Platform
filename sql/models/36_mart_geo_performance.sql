-- model: mart_geo_performance
-- materialized: table
-- depends_on: fact_orders, dim_customer
-- description: State-level performance with penetration, AOV and the service
--   metrics that vary hardest by geography in Brazil (delivery time, late
--   rate). Includes a lat/lng centroid so the map renders without a shapefile.

WITH geo AS (
    SELECT
        f.customer_state                                                AS state_code,
        f.customer_region                                               AS region,
        COUNT(*)                                                        AS orders,
        SUM(f.is_valid_sale)                                            AS valid_orders,
        COUNT(DISTINCT f.customer_key)                                  AS customers,
        SUM(CASE WHEN f.is_valid_sale = 1 THEN f.gross_revenue ELSE 0 END)   AS revenue,
        SUM(CASE WHEN f.is_valid_sale = 1 THEN f.freight_revenue ELSE 0 END) AS freight_revenue,
        AVG(f.review_score)                                             AS avg_review_score,
        AVG(f.days_to_deliver)                                          AS avg_days_to_deliver,
        SUM(COALESCE(f.is_late_delivery, 0))                            AS late_deliveries,
        SUM(f.is_delivered)                                             AS delivered_orders,
        SUM(f.is_canceled)                                              AS canceled_orders
    FROM fact_orders f
    WHERE f.customer_state IS NOT NULL
    GROUP BY f.customer_state, f.customer_region
),

centroid AS (
    SELECT
        state_code,
        AVG(latitude)  AS latitude,
        AVG(longitude) AS longitude
    FROM dim_customer
    WHERE latitude IS NOT NULL
    GROUP BY state_code
),

repeat_rate AS (
    SELECT state_code,
           {{ round2( 100.0 * SUM(is_repeat_customer) / NULLIF(COUNT(*), 0) ) }} AS repeat_customer_pct
    FROM dim_customer
    WHERE state_code IS NOT NULL
    GROUP BY state_code
)

SELECT
    g.state_code,
    g.region,
    g.orders,
    g.valid_orders,
    g.customers,
    g.canceled_orders,
    {{ round2(g.revenue) }}                                             AS revenue,
    {{ round2(g.freight_revenue) }}                                     AS freight_revenue,
    {{ round2( g.revenue / NULLIF(g.valid_orders, 0) ) }}               AS avg_order_value,
    {{ round2( g.revenue / NULLIF(g.customers, 0) ) }}                  AS revenue_per_customer,
    {{ round2( 100.0 * g.freight_revenue / NULLIF(g.revenue, 0) ) }}    AS freight_pct_of_revenue,
    {{ round2(g.avg_review_score) }}                                    AS avg_review_score,
    {{ round2(g.avg_days_to_deliver) }}                                 AS avg_days_to_deliver,
    {{ round2( 100.0 * g.late_deliveries / NULLIF(g.delivered_orders, 0) ) }}
                                                                        AS late_delivery_rate_pct,
    r.repeat_customer_pct,
    c.latitude,
    c.longitude,
    {{ round2( 100.0 * g.revenue / SUM(g.revenue) OVER () ) }}          AS pct_of_national_revenue,
    ROW_NUMBER() OVER (ORDER BY g.revenue DESC)                         AS revenue_rank
FROM geo g
LEFT JOIN centroid    c ON c.state_code = g.state_code
LEFT JOIN repeat_rate r ON r.state_code = g.state_code
