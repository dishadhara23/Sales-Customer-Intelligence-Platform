-- model: dim_geography
-- materialized: table
-- depends_on: stg_geolocation
-- description: One row per Brazilian postcode prefix, with a median coordinate
--   and a macro-region rollup. Region is the grain most sales questions are
--   actually asked at ("how is the North-East doing?") and is not in the raw
--   data, so it is derived from the state code here once.

SELECT
    CAST(geolocation_zip_code_prefix AS INTEGER) AS zip_code_prefix,
    geolocation_city                             AS city,
    UPPER(geolocation_state)                     AS state_code,
    {{ region(geolocation_state) }}              AS region,
    geolocation_lat                              AS latitude,
    geolocation_lng                              AS longitude,
    geolocation_points                           AS source_point_count
FROM stg_geolocation
