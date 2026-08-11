-- model: fact_order_items
-- materialized: table
-- depends_on: stg_order_items, stg_orders, stg_customers, dim_product, dim_seller, dim_customer
-- description: The line-item grain (one row per order_id + order_item_id).
--   This is the revenue grain: `price` is per unit shipped, so an order of
--   three identical mugs is three rows. Summing price at order level is the
--   most common way to get Olist revenue wrong.

SELECT
    oi.order_id || '-' || CAST(oi.order_item_id AS TEXT) AS order_item_key,
    oi.order_id,
    oi.order_item_id,
    c.customer_unique_id                                 AS customer_key,
    oi.product_id                                        AS product_key,
    oi.seller_id                                         AS seller_key,
    {{ date_key(o.order_purchase_timestamp) }}           AS date_key,
    {{ date(o.order_purchase_timestamp) }}               AS order_date,
    o.order_status,
    p.category,
    s.state_code                                         AS seller_state,
    d.state_code                                         AS customer_state,
    d.region                                             AS customer_region,
    oi.price                                             AS item_price,
    oi.freight_value,
    oi.price + oi.freight_value                          AS item_gross_value,
    {{ round2( 100.0 * oi.freight_value / NULLIF(oi.price + oi.freight_value, 0) ) }}
                                                         AS freight_pct_of_gross,
    CASE WHEN s.state_code = d.state_code THEN 1 ELSE 0 END AS is_intra_state_shipment,
    oi.shipping_limit_date
FROM stg_order_items oi
JOIN stg_orders     o ON o.order_id    = oi.order_id
JOIN stg_customers  c ON c.customer_id = o.customer_id
LEFT JOIN dim_product p ON p.product_key = oi.product_id
LEFT JOIN dim_seller  s ON s.seller_key  = oi.seller_id
LEFT JOIN dim_customer d ON d.customer_key = c.customer_unique_id
