-- model: mart_dash_kpi
-- materialized: table
-- depends_on: fact_orders, dim_customer
-- description: Pre-computed headline KPIs for every filter combination the
--   dashboard offers (region x year, including the 'ALL' rollups).
--
--   Why precompute instead of letting the browser add up monthly rows: customer
--   counts are DISTINCT, and distinct counts are not additive. Summing "active
--   customers" across three months double-counts anyone who bought twice, and
--   summing across regions double-counts anyone who moved. Materialising one
--   exact row per filter combination costs 24 rows and removes that whole class
--   of wrong-number bug from the front end.

WITH base AS (
    SELECT
        COALESCE(customer_region, 'Unknown')                        AS region,
        CAST({{ year(order_date) }} AS TEXT)                        AS year_label,
        customer_key,
        order_id,
        is_valid_sale,
        is_delivered,
        is_late_delivery,
        gross_revenue,
        freight_revenue,
        item_count,
        review_score,
        days_to_deliver
    FROM fact_orders
),

-- Four passes: region x year, region x all-years, all-regions x year,
-- all-regions x all-years. UNION ALL rather than GROUPING SETS because SQLite
-- has no GROUPING SETS and the models must compile on both backends.
combos AS (
    SELECT region, year_label FROM base GROUP BY region, year_label
    UNION ALL SELECT region, 'ALL' FROM base GROUP BY region
    UNION ALL SELECT 'ALL', year_label FROM base GROUP BY year_label
    UNION ALL SELECT 'ALL', 'ALL'
)

SELECT
    c.region,
    c.year_label,
    COUNT(*)                                                        AS orders,
    SUM(b.is_valid_sale)                                            AS valid_orders,
    COUNT(DISTINCT b.customer_key)                                  AS customers,
    {{ round2( SUM(CASE WHEN b.is_valid_sale = 1 THEN b.gross_revenue ELSE 0 END) ) }}
                                                                    AS revenue,
    {{ round2( SUM(CASE WHEN b.is_valid_sale = 1 THEN b.freight_revenue ELSE 0 END) ) }}
                                                                    AS freight_revenue,
    SUM(CASE WHEN b.is_valid_sale = 1 THEN b.item_count ELSE 0 END) AS units,
    {{ round2( AVG(b.review_score) ) }}                             AS avg_review_score,
    {{ round2( AVG(b.days_to_deliver) ) }}                          AS avg_days_to_deliver,
    {{ round2( 100.0 * SUM(COALESCE(b.is_late_delivery, 0))
               / NULLIF(SUM(CASE WHEN b.is_late_delivery IS NOT NULL THEN 1 ELSE 0 END), 0) ) }}
                                                                    AS late_delivery_pct
FROM combos c
JOIN base b
  ON (c.region = 'ALL' OR b.region = c.region)
 AND (c.year_label = 'ALL' OR b.year_label = c.year_label)
GROUP BY c.region, c.year_label
