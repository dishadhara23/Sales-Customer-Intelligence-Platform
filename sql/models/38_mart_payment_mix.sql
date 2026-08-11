-- model: mart_payment_mix
-- materialized: table
-- depends_on: fact_payments
-- description: Payment-instrument mix over time. In this market instalment
--   credit is a demand lever, not a back-office detail — basket size tracks
--   instalment availability closely — so the instrument split is a commercial
--   KPI rather than a finance footnote.

WITH dated AS (
    SELECT
        {{ month_start(order_date) }} AS month_start_date,
        {{ year_month(order_date) }}  AS year_month,
        payment_type,
        instalment_band,
        order_id,
        customer_key,
        payment_value,
        installments
    FROM fact_payments
    WHERE order_status NOT IN ('canceled', 'unavailable')
),

grouped AS (
    SELECT
        month_start_date,
        year_month,
        payment_type,
        instalment_band,
        COUNT(*)                        AS payment_rows,
        COUNT(DISTINCT order_id)        AS orders,
        COUNT(DISTINCT customer_key)    AS customers,
        SUM(payment_value)              AS payment_value,
        AVG(payment_value)              AS avg_payment_value,
        AVG(1.0 * installments)         AS avg_installments
    FROM dated
    GROUP BY month_start_date, year_month, payment_type, instalment_band
)

SELECT
    month_start_date,
    year_month,
    payment_type,
    instalment_band,
    payment_rows,
    orders,
    customers,
    {{ round2(payment_value) }}                                         AS payment_value,
    {{ round2(avg_payment_value) }}                                     AS avg_payment_value,
    {{ round2(avg_installments) }}                                      AS avg_installments,
    {{ round2( 100.0 * payment_value
               / SUM(payment_value) OVER (PARTITION BY month_start_date) ) }}
                                                                        AS pct_of_month_value
FROM grouped
