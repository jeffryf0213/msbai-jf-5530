# msbai-jf-5530

Class project for Dealing with Data: an ETL pipeline that loads Citibike data into BigQuery and serves a Streamlit dashboard joined to NYC weather.

## Dashboard

**Live URL (Cloud Run):** https://citibike-dashboard-312339790647.us-east1.run.app

Public — no login required. Shows how NYC weather affects Citibike ridership across the full 2013–present history.

Also available on Streamlit Cloud: https://msbai-jf-5530-hvqemrkuzisz9a6pqemm4p.streamlit.app

## BigQuery Objects

**Project:** `jf-5530`

| Object | Type | Description |
|--------|------|-------------|
| `jf-5530.citibike.trips` | Table | Raw unified trip data, full history (2013–present), both schemas normalized |
| `jf-5530.citibike.trips_clean` | View | Deduplicated, cast, region marker, Haversine distance |
| `jf-5530.citibike.daily_summary` | View | One row per date × region: trip counts, avg duration/distance, rider type breakdown |
| `jf-5530.citibike.daily` | Materialized Table | Pre-aggregated daily table joined with weather; partitioned by date. Used by the dashboard. |

## Repo Structure

```
etl/                  ETL scripts and verification SQL
dashboard/            Streamlit app + Dockerfile
CLAUDE.MD             Pipeline specification and decisions
DECISIONS.md          Business-language defense of all decisions + verification results
```

## Verification

See `etl/verify_counts.sql` for the queries used to cross-check trip counts against Citibike's published operating reports. Results summarized in `DECISIONS.md`.
