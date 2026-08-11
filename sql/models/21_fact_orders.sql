-- model: fact_orders
-- materialized: table
-- depends_on: stg_orders, stg_customers, stg_order_items, stg_order_payments, stg_order_reviews, dim_customer
-- description: One row per order — the headline fact table. Items, payments
--   and reviews are each pre-aggregated to the order grain in their own CTE
--   before joining, so the classic fan-out (an order with 3 items and 2
--   payment rows reporting 6x revenue) cannot occur.

WITH items AS (
    SELECT
        order_id,
        COUNT(*)                        AS item_count,
        COUNT(DISTINCT product_id)      AS distinct_product_count,
        COUNT(DISTINCT seller_id)       AS distinct_seller_count,
        SUM(price)                      AS item_revenue,
        SUM(freight_value)              AS freight_revenue,
        SUM(price + freight_value)      AS gross_revenue
    FROM stg_order_items
    GROUP BY order_id
),

payments AS (
    SELECT
        order_id,
        SUM(payment_value)              AS payment_value,
        MAX(payment_installments)       AS max_installments,
        COUNT(*)                        AS payment_row_count
    FROM stg_order_payments
    GROUP BY order_id
),

-- An order can be split across several payment methods; the one carrying the
-- largest value is the one worth attributing the order to.
primary_payment AS (
    SELECT order_id, payment_type
    FROM (
        SELECT
            order_id,
            payment_type,
            ROW_NUMBER() OVER (
                PARTITION BY order_id
                ORDER BY payment_value DESC, payment_sequential ASC
            ) AS rn
        FROM stg_order_payments
    ) ranked
    WHERE rn = 1
),

-- A handful of orders have two review rows. Keep the most recent.
latest_review AS (
    SELECT order_id, review_score, review_creation_date, has_comment
    FROM (
        SELECT
            order_id,
            review_score,
            review_creation_date,
            CASE WHEN review_comment_message IS NOT NULL
                  AND LENGTH(TRIM(review_comment_message)) > 0
                 THEN 1 ELSE 0 END AS has_comment,
            ROW_NUMBER() OVER (
                PARTITION BY order_id
                ORDER BY review_creation_date DESC, review_id DESC
            ) AS rn
        FROM stg_order_reviews
    ) ranked
    WHERE rn = 1
)

SELECT
    o.order_id,
    c.customer_unique_id                                        AS customer_key,
    o.customer_id                                               AS source_customer_id,
    o.order_status,
    {{ date_key(o.order_purchase_timestamp) }}                  AS date_key,
    {{ date(o.order_purchase_timestamp) }}                      AS order_date,
    {{ month_start(o.order_purchase_timestamp) }}               AS order_month,
    o.order_purchase_timestamp,
    o.order_approved_at,
    o.order_delivered_carrier_date,
    o.order_delivered_customer_date,
    o.order_estimated_delivery_date,

    dc.state_code                                               AS customer_state,
    dc.region                                                   AS customer_region,
    dc.city                                                     AS customer_city,
    dc.cohort_month,
    dc.is_repeat_customer,

    COALESCE(i.item_count, 0)                                   AS item_count,
    COALESCE(i.distinct_product_count, 0)                       AS distinct_product_count,
    COALESCE(i.distinct_seller_count, 0)                        AS distinct_seller_count,
    COALESCE(i.item_revenue, 0)                                 AS item_revenue,
    COALESCE(i.freight_revenue, 0)                              AS freight_revenue,
    COALESCE(i.gross_revenue, 0)                                AS gross_revenue,

    COALESCE(p.payment_value, 0)                                AS payment_value,
    COALESCE(p.max_installments, 0)                             AS max_installments,
    pp.payment_type                                             AS primary_payment_type,

    r.review_score,
    r.has_comment                                               AS review_has_comment,

    -- Fulfilment timings. NULL (not zero) where the milestone never happened,
    -- so AVG() over these silently excludes undelivered orders instead of
    -- dragging the average toward zero.
    CASE WHEN o.order_approved_at IS NOT NULL
         THEN {{ days_between(o.order_approved_at, o.order_purchase_timestamp) }} END
                                                                AS days_to_approve,
    CASE WHEN o.order_delivered_customer_date IS NOT NULL
         THEN {{ days_between(o.order_delivered_customer_date, o.order_purchase_timestamp) }} END
                                                                AS days_to_deliver,
    CASE WHEN o.order_delivered_customer_date IS NOT NULL
         THEN {{ days_between(o.order_delivered_customer_date, o.order_estimated_delivery_date) }} END
                                                                AS delivery_vs_estimate_days,
    CASE
        WHEN o.order_delivered_customer_date IS NULL THEN NULL
        WHEN {{ days_between(o.order_delivered_customer_date, o.order_estimated_delivery_date) }} > 0
             THEN 1 ELSE 0
    END                                                         AS is_late_delivery,

    CASE WHEN o.order_status = 'delivered' THEN 1 ELSE 0 END    AS is_delivered,
    CASE WHEN o.order_status = 'canceled'  THEN 1 ELSE 0 END    AS is_canceled,
    CASE WHEN o.order_status IN ('delivered', 'shipped', 'invoiced',
                                 'processing', 'approved')
         THEN 1 ELSE 0 END                                      AS is_valid_sale
FROM stg_orders o
JOIN stg_customers c   ON c.customer_id  = o.customer_id
LEFT JOIN dim_customer dc ON dc.customer_key = c.customer_unique_id
LEFT JOIN items i         ON i.order_id  = o.order_id
LEFT JOIN payments p      ON p.order_id  = o.order_id
LEFT JOIN primary_payment pp ON pp.order_id = o.order_id
LEFT JOIN latest_review r ON r.order_id  = o.order_id
