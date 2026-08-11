-- model: mart_dash_category
-- materialized: table
-- depends_on: fact_order_items
-- description: Category revenue by month and region, with an 'ALL' region
--   rollup. Every category is kept — no 'other' bucket.
--
--   An earlier version folded everything outside the top 20 into 'other', which
--   made 'other' the single largest bar and destroyed the Pareto reading: the
--   chart's whole point is that revenue is spread thin, and a synthetic bucket
--   collecting the thin tail hides exactly that. Truncation for readability
--   belongs in the chart (top N shown), not in the data.

WITH labelled AS (
    SELECT
        {{ year_month(oi.order_date) }}                     AS year_month,
        COALESCE(oi.customer_region, 'Unknown')             AS region,
        oi.category,
        oi.item_gross_value,
        oi.freight_value,
        oi.order_id
    FROM fact_order_items oi
    WHERE oi.order_status NOT IN ('canceled', 'unavailable')
),

scoped AS (
    SELECT year_month, region, category, item_gross_value, freight_value, order_id
    FROM labelled
    UNION ALL
    SELECT year_month, 'ALL', category, item_gross_value, freight_value, order_id
    FROM labelled
)

SELECT
    year_month,
    region,
    category,
    COUNT(*)                                        AS units,
    COUNT(DISTINCT order_id)                        AS orders,
    {{ round2( SUM(item_gross_value) ) }}           AS revenue,
    {{ round2( SUM(freight_value) ) }}              AS freight
FROM scoped
GROUP BY year_month, region, category
