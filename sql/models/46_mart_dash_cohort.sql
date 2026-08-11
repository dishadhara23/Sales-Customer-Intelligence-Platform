-- model: mart_dash_cohort
-- materialized: table
-- depends_on: fact_orders, dim_customer
-- description: Cohort retention split by the customer's region, plus 'ALL'.
--   A customer belongs to exactly one region here (their most recent address),
--   so cohort sizes still add up across regions and the grid stays honest under
--   a region filter.

WITH first_valid AS (
    SELECT
        f.customer_key,
        {{ month_start( MIN(f.order_date) ) }}  AS cohort_month,
        MIN(COALESCE(f.customer_region, 'Unknown')) AS region
    FROM fact_orders f
    WHERE f.is_valid_sale = 1
    GROUP BY f.customer_key
),

scoped_customers AS (
    SELECT customer_key, cohort_month, region FROM first_valid
    UNION ALL
    SELECT customer_key, cohort_month, 'ALL' FROM first_valid
),

cohort_size AS (
    SELECT cohort_month, region, COUNT(*) AS cohort_customers
    FROM scoped_customers
    GROUP BY cohort_month, region
),

activity AS (
    SELECT
        sc.cohort_month,
        sc.region,
        {{ months_between(f.order_date, sc.cohort_month) }} AS months_since_first_order,
        COUNT(DISTINCT f.customer_key)                      AS active_customers,
        {{ round2( SUM(f.gross_revenue) ) }}                AS revenue
    FROM fact_orders f
    JOIN scoped_customers sc ON sc.customer_key = f.customer_key
    WHERE f.is_valid_sale = 1
    GROUP BY sc.cohort_month, sc.region,
             {{ months_between(f.order_date, sc.cohort_month) }}
)

SELECT
    a.cohort_month,
    {{ year_month(a.cohort_month) }}                        AS cohort_label,
    a.region,
    a.months_since_first_order,
    cs.cohort_customers,
    a.active_customers,
    a.revenue,
    {{ round2( 100.0 * a.active_customers / NULLIF(cs.cohort_customers, 0) ) }}
                                                            AS retention_pct
FROM activity a
JOIN cohort_size cs
  ON cs.cohort_month = a.cohort_month AND cs.region = a.region
WHERE a.months_since_first_order >= 0
  AND a.months_since_first_order <= 11
