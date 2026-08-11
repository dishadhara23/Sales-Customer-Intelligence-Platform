-- model: fact_payments
-- materialized: table
-- depends_on: stg_order_payments, fact_orders
-- description: Payment-instrument grain (order_id + payment_sequential), kept
--   separate from fact_orders so instrument mix and instalment behaviour can
--   be analysed without re-introducing the payment fan-out into revenue.

SELECT
    pay.order_id || '-' || CAST(pay.payment_sequential AS TEXT) AS payment_key,
    pay.order_id,
    pay.payment_sequential,
    pay.payment_type,
    pay.payment_installments                                    AS installments,
    pay.payment_value,
    o.customer_key,
    o.date_key,
    o.order_date,
    o.customer_state,
    o.customer_region,
    o.order_status,
    CASE WHEN pay.payment_installments > 1 THEN 1 ELSE 0 END     AS is_instalment_plan,
    CASE
        WHEN pay.payment_installments <= 1  THEN 'single payment'
        WHEN pay.payment_installments <= 3  THEN '2-3 instalments'
        WHEN pay.payment_installments <= 6  THEN '4-6 instalments'
        WHEN pay.payment_installments <= 12 THEN '7-12 instalments'
        ELSE '12+ instalments'
    END                                                         AS instalment_band
FROM stg_order_payments pay
JOIN fact_orders o ON o.order_id = pay.order_id
