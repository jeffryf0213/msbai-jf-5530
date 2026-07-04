-- =============================================================================
-- Citibike pipeline verification queries
-- Project: jf-5530
-- Run these in BigQuery to confirm no month is missing or double-counted.
-- =============================================================================

-- 1. Yearly + region counts from our materialized daily table
SELECT
  EXTRACT(YEAR FROM trip_date) AS year,
  region,
  SUM(num_trips)               AS total_trips,
  COUNT(DISTINCT trip_date)    AS days_covered
FROM `jf-5530.citibike.daily`
GROUP BY 1, 2
ORDER BY 1, 2;


-- 2. Monthly counts (for spot-check against operating reports)
SELECT
  FORMAT_DATE('%Y-%m', trip_date) AS month,
  region,
  SUM(num_trips)                  AS total_trips
FROM `jf-5530.citibike.daily`
GROUP BY 1, 2
ORDER BY 1, 2;


-- 3. Check for missing months (any gap in the time series?)
WITH months AS (
  SELECT
    FORMAT_DATE('%Y-%m', trip_date) AS month,
    region
  FROM `jf-5530.citibike.daily`
  GROUP BY 1, 2
),
expected AS (
  -- Generate every month from 2013-06 through today
  SELECT
    FORMAT_DATE('%Y-%m', month_start) AS month
  FROM UNNEST(
    GENERATE_DATE_ARRAY(DATE '2013-06-01', CURRENT_DATE(), INTERVAL 1 MONTH)
  ) AS month_start
)
SELECT
  e.month,
  COALESCE(m_nyc.region, 'MISSING') AS nyc_status,
  COALESCE(m_jc.region,  'MISSING') AS jc_status
FROM expected e
LEFT JOIN months m_nyc ON e.month = m_nyc.month AND m_nyc.region = 'NYC'
LEFT JOIN months m_jc  ON e.month = m_jc.month  AND m_jc.region  = 'JC'
WHERE m_nyc.region IS NULL OR m_jc.region IS NULL   -- only show gaps
ORDER BY 1;
-- Expected: 0 rows for NYC from 2013-06 onward.
-- JC gaps before ~2015-10 are expected (source files don't exist).


-- 4. Check for double-counting (duplicate source_file loads)
SELECT
  source_file,
  COUNT(*) AS row_count
FROM `jf-5530.citibike.trips`
GROUP BY source_file
HAVING COUNT(*) > 1
ORDER BY row_count DESC
LIMIT 20;
-- Expected: every source_file appears exactly once in the group-by,
-- meaning no zip was loaded twice.


-- 5. Spot-check: July 2023 NYC (compare to operating report: 1,282,941)
SELECT
  SUM(num_trips) AS loaded_trips,
  1282941        AS operating_report,
  ROUND(ABS(SUM(num_trips) - 1282941) / 1282941 * 100, 2) AS pct_diff
FROM `jf-5530.citibike.daily`
WHERE region = 'NYC'
  AND FORMAT_DATE('%Y-%m', trip_date) = '2023-07';
-- Expected: pct_diff < 1.0


-- 6. Spot-check: January 2024 NYC (compare to operating report: 1,091,022)
SELECT
  SUM(num_trips) AS loaded_trips,
  1091022        AS operating_report,
  ROUND(ABS(SUM(num_trips) - 1091022) / 1091022 * 100, 2) AS pct_diff
FROM `jf-5530.citibike.daily`
WHERE region = 'NYC'
  AND FORMAT_DATE('%Y-%m', trip_date) = '2024-01';
-- Expected: pct_diff < 1.0


-- 7. Spot-check: June 2024 NYC (compare to operating report: 2,031,887)
SELECT
  SUM(num_trips) AS loaded_trips,
  2031887        AS operating_report,
  ROUND(ABS(SUM(num_trips) - 2031887) / 2031887 * 100, 2) AS pct_diff
FROM `jf-5530.citibike.daily`
WHERE region = 'NYC'
  AND FORMAT_DATE('%Y-%m', trip_date) = '2024-06';
-- Expected: pct_diff < 1.0
