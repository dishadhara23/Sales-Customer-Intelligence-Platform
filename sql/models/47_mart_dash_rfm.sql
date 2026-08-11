-- model: mart_dash_rfm
-- materialized: table
-- depends_on: mart_rfm
-- description: RFM segment sizes and value by region, plus 'ALL'. Scores
--   themselves are computed nationally in mart_rfm and are not re-scored per
--   region — a "Champion" should mean the same thing everywhere, otherwise the
--   segment stops being comparable across the filter.

WITH scoped AS (
    SELECT COALESCE(region, 'Unknown') AS region, rfm_segment, monetary_value,
           frequency_orders, recency_days
    FROM mart_rfm
    UNION ALL
    SELECT 'ALL', rfm_segment, monetary_value, frequency_orders, recency_days
    FROM mart_rfm
)

SELECT
    region,
    rfm_segment                                     AS segment,
    COUNT(*)                                        AS customers,
    {{ round2( SUM(monetary_value) ) }}             AS revenue,
    {{ round2( AVG(monetary_value) ) }}             AS avg_value,
    {{ round2( AVG(1.0 * frequency_orders) ) }}     AS avg_orders,
    {{ round2( AVG(1.0 * recency_days) ) }}         AS avg_recency_days
FROM scoped
GROUP BY region, rfm_segment
