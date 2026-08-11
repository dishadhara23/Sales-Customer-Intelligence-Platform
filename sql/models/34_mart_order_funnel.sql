-- model: mart_order_funnel
-- materialized: table
-- depends_on: fact_orders
-- description: Fulfilment funnel by month, measured on *milestone timestamps*
--   rather than the terminal `order_status` string.
--
--   Status is a snapshot of where an order ended up; the timestamps record
--   which stages it actually passed through. Building the funnel on timestamps
--   is what makes stage-to-stage conversion (and therefore the drop-off) real
--   rather than a re-labelling of the same terminal state.

WITH stages AS (
    SELECT
        {{ month_start(order_date) }} AS month_start_date,
        1 AS stage_order, 'Purchased'          AS stage,
        COUNT(*)                                                                  AS orders
    FROM fact_orders GROUP BY {{ month_start(order_date) }}

    UNION ALL
    SELECT {{ month_start(order_date) }}, 2, 'Payment approved',
        SUM(CASE WHEN order_approved_at IS NOT NULL THEN 1 ELSE 0 END)
    FROM fact_orders GROUP BY {{ month_start(order_date) }}

    UNION ALL
    SELECT {{ month_start(order_date) }}, 3, 'Handed to carrier',
        SUM(CASE WHEN order_delivered_carrier_date IS NOT NULL THEN 1 ELSE 0 END)
    FROM fact_orders GROUP BY {{ month_start(order_date) }}

    UNION ALL
    SELECT {{ month_start(order_date) }}, 4, 'Delivered to customer',
        SUM(CASE WHEN order_delivered_customer_date IS NOT NULL THEN 1 ELSE 0 END)
    FROM fact_orders GROUP BY {{ month_start(order_date) }}

    UNION ALL
    SELECT {{ month_start(order_date) }}, 5, 'Delivered on time',
        SUM(CASE WHEN order_delivered_customer_date IS NOT NULL
                  AND COALESCE(is_late_delivery, 0) = 0 THEN 1 ELSE 0 END)
    FROM fact_orders GROUP BY {{ month_start(order_date) }}
),

ranked AS (
    SELECT
        month_start_date,
        stage_order,
        stage,
        orders,
        FIRST_VALUE(orders) OVER (
            PARTITION BY month_start_date ORDER BY stage_order
        )                                                       AS top_of_funnel,
        LAG(orders) OVER (
            PARTITION BY month_start_date ORDER BY stage_order
        )                                                       AS prev_stage_orders
    FROM stages
)

SELECT
    month_start_date,
    {{ year_month(month_start_date) }}                          AS year_month,
    stage_order,
    stage,
    orders,
    top_of_funnel,
    COALESCE(prev_stage_orders - orders, 0)                     AS dropped_from_prev_stage,
    {{ round2( 100.0 * orders / NULLIF(top_of_funnel, 0) ) }}    AS pct_of_top_of_funnel,
    {{ round2( 100.0 * orders / NULLIF(prev_stage_orders, 0) ) }} AS stage_conversion_pct
FROM ranked
