-- model: mart_cohort_retention
-- materialized: table
-- depends_on: fact_orders, dim_customer
-- description: Monthly acquisition cohorts x months-since-first-order, with
--   both customer retention and revenue retention.
--
--   Cohorts are built on customer_unique_id (see dim_customer). Built on
--   customer_id — the raw per-order key — every cell after month 0 would be
--   empty, which is the single most common way this dataset is mis-analysed.

-- The cohort anchor is the customer's first *valid* order, computed here
-- rather than reused from dim_customer.cohort_month. dim_customer anchors on
-- the first order of any kind, including cancelled ones — mixing the two
-- definitions makes month 0 land below 100%, because a customer counted in the
-- denominator can be absent from the numerator.
WITH first_valid AS (
    SELECT
        customer_key,
        {{ month_start( MIN(order_date) ) }} AS cohort_month
    FROM fact_orders
    WHERE is_valid_sale = 1
    GROUP BY customer_key
),

cohort_size AS (
    SELECT cohort_month, COUNT(*) AS cohort_customers
    FROM first_valid
    GROUP BY cohort_month
),

activity AS (
    SELECT
        fv.cohort_month,
        {{ months_between(f.order_date, fv.cohort_month) }} AS months_since_first_order,
        COUNT(DISTINCT f.customer_key)                      AS active_customers,
        SUM(f.gross_revenue)                                AS revenue,
        COUNT(DISTINCT f.order_id)                          AS orders
    FROM fact_orders f
    JOIN first_valid fv ON fv.customer_key = f.customer_key
    WHERE f.is_valid_sale = 1
    GROUP BY fv.cohort_month, {{ months_between(f.order_date, fv.cohort_month) }}
)

SELECT
    a.cohort_month,
    {{ year_month(a.cohort_month) }}                        AS cohort_label,
    a.months_since_first_order,
    cs.cohort_customers,
    a.active_customers,
    a.orders,
    {{ round2(a.revenue) }}                                 AS revenue,
    {{ round2( 100.0 * a.active_customers / NULLIF(cs.cohort_customers, 0) ) }}
                                                            AS retention_pct,
    {{ round2( a.revenue / NULLIF(cs.cohort_customers, 0) ) }}
                                                            AS revenue_per_cohort_customer,
    {{ round2( a.revenue / NULLIF(a.active_customers, 0) ) }} AS revenue_per_active_customer
FROM activity a
JOIN cohort_size cs ON cs.cohort_month = a.cohort_month
WHERE a.months_since_first_order >= 0
