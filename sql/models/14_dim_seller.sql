-- model: dim_seller
-- materialized: table
-- depends_on: stg_sellers, dim_geography
-- description: Marketplace sellers, enriched with the same region rollup as
--   customers so seller-side and buyer-side geography can be compared on one
--   axis (the "how far did this parcel travel" question).

SELECT
    s.seller_id                                          AS seller_key,
    s.seller_zip_code_prefix                             AS zip_code_prefix,
    COALESCE(g.city, s.seller_city)                      AS city,
    UPPER(s.seller_state)                                AS state_code,
    {{ region(s.seller_state) }}                         AS region,
    g.latitude,
    g.longitude
FROM stg_sellers s
LEFT JOIN dim_geography g
       ON g.zip_code_prefix = CAST(s.seller_zip_code_prefix AS INTEGER)
