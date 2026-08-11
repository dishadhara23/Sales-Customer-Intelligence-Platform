-- model: mart_customer_360
-- materialized: view
-- depends_on: dim_customer, mart_rfm
-- description: The single wide table a non-technical user (or the LLM agent)
--   should reach for when the question is about *customers*. Materialised as a
--   view because it is a convenience join over two tables that are already
--   physically built — no need to store the rows twice.

SELECT
    dc.customer_key,
    dc.city,
    dc.state_code,
    dc.region,
    dc.latitude,
    dc.longitude,
    dc.first_order_date,
    dc.last_order_date,
    dc.cohort_month,
    dc.lifetime_order_count,
    dc.is_repeat_customer,
    r.recency_days,
    r.frequency_orders,
    r.monetary_value,
    r.avg_order_value,
    r.lifetime_units,
    r.avg_review_score,
    r.r_score,
    r.f_score,
    r.m_score,
    r.rfm_total,
    r.rfm_cell,
    r.rfm_segment,
    CASE
        WHEN r.monetary_value IS NULL          THEN 'no valid purchase'
        WHEN r.monetary_value >= 1000          THEN 'high value (R$1k+)'
        WHEN r.monetary_value >=  300          THEN 'mid value (R$300-1k)'
        WHEN r.monetary_value >=  100          THEN 'low value (R$100-300)'
        ELSE 'minimal (<R$100)'
    END                                        AS value_tier
FROM dim_customer dc
LEFT JOIN mart_rfm r ON r.customer_key = dc.customer_key
