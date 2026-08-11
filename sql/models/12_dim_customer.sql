-- model: dim_customer
-- materialized: table
-- depends_on: stg_customers, stg_orders, dim_geography
-- description: One row per *person*, keyed on customer_unique_id.
--
--   This is the single most important modelling decision in the project. Olist
--   mints a fresh `customer_id` for every order, so joining on it makes every
--   buyer look like a first-time buyer: repeat rate collapses to 0% and cohort
--   retention is a flat line. `customer_unique_id` is the real person key, and
--   collapsing to it here is what makes RFM and retention meaningful at all.

WITH person_orders AS (
    SELECT
        c.customer_unique_id,
        o.order_id,
        o.order_purchase_timestamp,
        c.customer_id,
        c.customer_zip_code_prefix,
        c.customer_city,
        c.customer_state,
        ROW_NUMBER() OVER (
            PARTITION BY c.customer_unique_id
            ORDER BY o.order_purchase_timestamp DESC, o.order_id DESC
        ) AS recency_rank
    FROM stg_customers c
    JOIN stg_orders    o ON o.customer_id = c.customer_id
),

person AS (
    SELECT
        customer_unique_id,
        COUNT(DISTINCT order_id)                        AS lifetime_order_count,
        {{ date( MIN(order_purchase_timestamp) ) }}     AS first_order_date,
        {{ date( MAX(order_purchase_timestamp) ) }}     AS last_order_date,
        COUNT(DISTINCT customer_id)                     AS source_customer_id_count
    FROM person_orders
    GROUP BY customer_unique_id
),

-- Address can change between orders; the most recent one is the useful one.
latest_address AS (
    SELECT
        customer_unique_id,
        customer_zip_code_prefix,
        customer_city,
        customer_state
    FROM person_orders
    WHERE recency_rank = 1
)

SELECT
    p.customer_unique_id                                       AS customer_key,
    a.customer_zip_code_prefix                                 AS zip_code_prefix,
    COALESCE(g.city, a.customer_city)                          AS city,
    UPPER(a.customer_state)                                    AS state_code,
    -- Derived from the customer's own state, never from the geo join: a
    -- missing postcode match would otherwise emit region 'Unknown' for a state
    -- that also appears with its real region, splitting every regional total.
    {{ region(a.customer_state) }}                             AS region,
    g.latitude,
    g.longitude,
    p.first_order_date,
    p.last_order_date,
    {{ month_start(p.first_order_date) }}                      AS cohort_month,
    p.lifetime_order_count,
    CASE WHEN p.lifetime_order_count > 1 THEN 1 ELSE 0 END     AS is_repeat_customer,
    p.source_customer_id_count
FROM person p
JOIN latest_address a ON a.customer_unique_id = p.customer_unique_id
LEFT JOIN dim_geography g
       ON g.zip_code_prefix = CAST(a.customer_zip_code_prefix AS INTEGER)
