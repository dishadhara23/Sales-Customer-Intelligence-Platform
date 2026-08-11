-- model: dim_date
-- materialized: table
-- depends_on: stg_orders
-- description: Contiguous daily calendar spanning the order history, plus the
--   fiscal attributes every time-series KPI groups by. Built as a recursive
--   date spine (not SELECT DISTINCT) so days with zero orders still exist and
--   trend charts show real troughs instead of silently closing the gap.

WITH RECURSIVE bounds AS (
    SELECT
        {{ month_start( MIN(order_purchase_timestamp) ) }} AS start_date,
        {{ date( MAX(order_purchase_timestamp) ) }}        AS end_date
    FROM stg_orders
    WHERE order_purchase_timestamp IS NOT NULL
),

spine AS (
    SELECT start_date AS d, end_date FROM bounds
    UNION ALL
    SELECT {{ add_days(d, 1) }}, end_date
    FROM spine
    WHERE d < end_date
)

SELECT
    {{ date_key(d) }}                                  AS date_key,
    d                                                  AS calendar_date,
    {{ year(d) }}                                      AS calendar_year,
    {{ quarter(d) }}                                   AS calendar_quarter,
    {{ month(d) }}                                     AS calendar_month,
    {{ year_month(d) }}                                AS year_month,
    {{ month_start(d) }}                               AS month_start_date,
    {{ dow(d) }}                                       AS day_of_week,
    CASE {{ month(d) }}
        WHEN  1 THEN 'Jan' WHEN  2 THEN 'Feb' WHEN  3 THEN 'Mar'
        WHEN  4 THEN 'Apr' WHEN  5 THEN 'May' WHEN  6 THEN 'Jun'
        WHEN  7 THEN 'Jul' WHEN  8 THEN 'Aug' WHEN  9 THEN 'Sep'
        WHEN 10 THEN 'Oct' WHEN 11 THEN 'Nov' ELSE 'Dec'
    END                                                AS month_name,
    CASE {{ dow(d) }}
        WHEN 0 THEN 'Sunday'    WHEN 1 THEN 'Monday'  WHEN 2 THEN 'Tuesday'
        WHEN 3 THEN 'Wednesday' WHEN 4 THEN 'Thursday' WHEN 5 THEN 'Friday'
        ELSE 'Saturday'
    END                                                AS day_name,
    CASE WHEN {{ dow(d) }} IN (0, 6) THEN 1 ELSE 0 END AS is_weekend,
    CAST({{ quarter(d) }} AS TEXT)                     AS quarter_label
FROM spine
