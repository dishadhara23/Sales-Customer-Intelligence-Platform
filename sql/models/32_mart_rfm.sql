-- model: mart_rfm
-- materialized: table
-- depends_on: fact_orders, dim_customer
-- description: RFM scoring and segmentation, one row per customer.
--
--   Deliberate deviation from textbook RFM: Frequency uses fixed buckets, not
--   NTILE(5). ~97% of Olist customers buy exactly once, so a frequency
--   quintile would slice an all-1s column into five meaningless groups and
--   manufacture "loyalty" tiers that do not exist. Fixed buckets keep the
--   score honest — most customers legitimately score F=1 — and the resulting
--   segment sizes match what the business would recognise.
--
--   Recency is measured against the dataset's last order date, not today's
--   date, so scores are reproducible rather than drifting with wall-clock time.

WITH snapshot AS (
    SELECT MAX(order_date) AS as_of_date FROM fact_orders WHERE is_valid_sale = 1
),

base AS (
    SELECT
        f.customer_key,
        COUNT(DISTINCT f.order_id)                                  AS frequency_orders,
        SUM(f.gross_revenue)                                        AS monetary_value,
        MAX(f.order_date)                                           AS last_order_date,
        MIN(f.order_date)                                           AS first_order_date,
        AVG(f.review_score)                                         AS avg_review_score,
        SUM(f.item_count)                                           AS lifetime_units
    FROM fact_orders f
    WHERE f.is_valid_sale = 1
    GROUP BY f.customer_key
),

scored AS (
    SELECT
        b.*,
        s.as_of_date,
        {{ days_between(s.as_of_date, b.last_order_date) }}          AS recency_days,
        -- Recency quintile inverted: most recent buyers score 5.
        --
        -- customer_key is a tiebreaker, not decoration. Thousands of customers
        -- share an identical recency or spend, and NTILE has to split those ties
        -- somewhere. Without a deterministic second sort key the split depends on
        -- whatever row order the engine happens to produce — PostgreSQL and
        -- SQLite disagreed by ~2 customers per segment, so the same data gave two
        -- different segmentations. Scores must be reproducible to be trusted.
        6 - NTILE(5) OVER (
            ORDER BY {{ days_between(s.as_of_date, b.last_order_date) }} ASC,
                     b.customer_key ASC
        )                                                           AS r_score,
        CASE
            WHEN b.frequency_orders >= 5 THEN 5
            WHEN b.frequency_orders = 4  THEN 4
            WHEN b.frequency_orders = 3  THEN 3
            WHEN b.frequency_orders = 2  THEN 2
            ELSE 1
        END                                                         AS f_score,
        NTILE(5) OVER (ORDER BY b.monetary_value ASC, b.customer_key ASC) AS m_score
    FROM base b
    CROSS JOIN snapshot s
)

SELECT
    sc.customer_key,
    dc.state_code,
    dc.region,
    dc.city,
    sc.as_of_date,
    sc.first_order_date,
    sc.last_order_date,
    sc.recency_days,
    sc.frequency_orders,
    sc.lifetime_units,
    {{ round2(sc.monetary_value) }}                                 AS monetary_value,
    {{ round2( sc.monetary_value / NULLIF(sc.frequency_orders, 0) ) }} AS avg_order_value,
    {{ round2(sc.avg_review_score) }}                               AS avg_review_score,
    sc.r_score,
    sc.f_score,
    sc.m_score,
    sc.r_score + sc.f_score + sc.m_score                            AS rfm_total,
    CAST(sc.r_score AS TEXT) || CAST(sc.f_score AS TEXT) || CAST(sc.m_score AS TEXT)
                                                                    AS rfm_cell,
    CASE
        WHEN sc.r_score >= 4 AND sc.f_score >= 4                     THEN 'Champions'
        WHEN sc.r_score >= 3 AND sc.f_score >= 3                     THEN 'Loyal'
        WHEN sc.r_score >= 4 AND sc.f_score <= 2 AND sc.m_score >= 4 THEN 'Big Spender (new)'
        WHEN sc.r_score >= 4 AND sc.f_score <= 2                     THEN 'Recent / One-time'
        WHEN sc.r_score = 3  AND sc.f_score <= 2                     THEN 'Promising'
        WHEN sc.r_score <= 2 AND sc.f_score >= 3                     THEN 'At Risk'
        WHEN sc.r_score <= 2 AND sc.m_score >= 4                     THEN 'Cannot Lose Them'
        WHEN sc.r_score = 2                                          THEN 'Hibernating'
        ELSE 'Lost'
    END                                                             AS rfm_segment
FROM scored sc
LEFT JOIN dim_customer dc ON dc.customer_key = sc.customer_key
