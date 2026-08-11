-- model: dim_product
-- materialized: table
-- depends_on: stg_products, stg_category_translation
-- description: Product catalogue with English category names and a derived
--   size band. ~600 products carry no category at all; they are labelled
--   'uncategorised' rather than dropped, so category revenue still reconciles
--   to total revenue.

SELECT
    p.product_id                                        AS product_key,
    COALESCE(t.product_category_name_english,
             p.product_category_name,
             'uncategorised')                           AS category,
    COALESCE(p.product_category_name, 'uncategorised')  AS category_pt,
    p.product_weight_g                                  AS weight_g,
    p.product_length_cm                                 AS length_cm,
    p.product_height_cm                                 AS height_cm,
    p.product_width_cm                                  AS width_cm,
    COALESCE(p.product_length_cm, 0)
      * COALESCE(p.product_height_cm, 0)
      * COALESCE(p.product_width_cm, 0)                 AS volume_cm3,
    p.product_photos_qty                                AS photo_count,
    p.product_name_lenght                               AS name_length,
    p.product_description_lenght                        AS description_length,
    CASE
        WHEN p.product_weight_g IS NULL      THEN 'unknown'
        WHEN p.product_weight_g <   500      THEN 'light (<0.5kg)'
        WHEN p.product_weight_g <  2000      THEN 'medium (0.5-2kg)'
        WHEN p.product_weight_g < 10000      THEN 'heavy (2-10kg)'
        ELSE 'bulky (10kg+)'
    END                                                 AS weight_band,
    CASE WHEN p.product_category_name IS NULL THEN 1 ELSE 0 END AS is_uncategorised
FROM stg_products p
LEFT JOIN stg_category_translation t
       ON t.product_category_name = p.product_category_name
