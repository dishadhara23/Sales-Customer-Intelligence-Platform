-- model: mart_category_performance
-- materialized: table
-- depends_on: fact_order_items, fact_orders, dim_product
-- description: Product-category P&L view with revenue concentration (running
--   share for Pareto / ABC analysis) and the freight burden per category —
--   the number that decides whether a heavy category is actually profitable.

WITH cat AS (
    SELECT
        oi.category,
        COUNT(*)                                    AS units_sold,
        COUNT(DISTINCT oi.order_id)                 AS orders,
        COUNT(DISTINCT oi.customer_key)             AS customers,
        COUNT(DISTINCT oi.product_key)              AS distinct_products,
        COUNT(DISTINCT oi.seller_key)               AS distinct_sellers,
        SUM(oi.item_price)                          AS item_revenue,
        SUM(oi.freight_value)                       AS freight_cost,
        SUM(oi.item_gross_value)                    AS gross_revenue,
        AVG(oi.item_price)                          AS avg_unit_price
    FROM fact_order_items oi
    WHERE oi.order_status NOT IN ('canceled', 'unavailable')
    GROUP BY oi.category
),

quality AS (
    SELECT
        oi.category,
        AVG(o.review_score)                         AS avg_review_score,
        AVG(o.days_to_deliver)                      AS avg_days_to_deliver,
        {{ round2( 100.0 * SUM(COALESCE(o.is_late_delivery, 0))
                   / NULLIF(SUM(CASE WHEN o.is_delivered = 1 THEN 1 ELSE 0 END), 0) ) }}
                                                    AS late_delivery_rate_pct
    FROM fact_order_items oi
    JOIN fact_orders o ON o.order_id = oi.order_id
    GROUP BY oi.category
)

SELECT
    c.category,
    c.units_sold,
    c.orders,
    c.customers,
    c.distinct_products,
    c.distinct_sellers,
    {{ round2(c.gross_revenue) }}                                       AS gross_revenue,
    {{ round2(c.item_revenue) }}                                        AS item_revenue,
    {{ round2(c.freight_cost) }}                                        AS freight_cost,
    {{ round2(c.avg_unit_price) }}                                      AS avg_unit_price,
    {{ round2( c.gross_revenue / NULLIF(c.orders, 0) ) }}               AS revenue_per_order,
    {{ round2( 100.0 * c.freight_cost / NULLIF(c.gross_revenue, 0) ) }} AS freight_pct_of_revenue,
    {{ round2(q.avg_review_score) }}                                    AS avg_review_score,
    {{ round2(q.avg_days_to_deliver) }}                                 AS avg_days_to_deliver,
    q.late_delivery_rate_pct,
    ROW_NUMBER() OVER (ORDER BY c.gross_revenue DESC)                   AS revenue_rank,
    {{ round2( 100.0 * c.gross_revenue / SUM(c.gross_revenue) OVER () ) }}
                                                                        AS pct_of_total_revenue,
    {{ round2( 100.0 * SUM(c.gross_revenue) OVER (ORDER BY c.gross_revenue DESC
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
               / SUM(c.gross_revenue) OVER () ) }}                      AS cumulative_revenue_pct
FROM cat c
LEFT JOIN quality q ON q.category = c.category
