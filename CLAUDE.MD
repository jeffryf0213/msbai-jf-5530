

## Pipeline Specification

### Scope

All Citibike trips ever recorded — NYC and Jersey City both included. The `region` column (`NYC` or `JC`) is derived from the source filename: filenames containing `JC` map to `JC`, all others to `NYC`.

### Distance

Haversine straight-line distance is computed in BigQuery from `start_lat`/`start_lng` and `end_lat`/`end_lng`. **This is an estimate that understates actual riding distance** (real routes follow streets and paths, not straight lines). Distance is `NULL` when either coordinate pair is missing.

### Day Definition

A trip counts toward the day its `started_at` timestamp falls in, converted to **US/Eastern time** (respecting DST). This aligns with the weather dataset join on `nyu-datasets.weather.m_weather_daily_nyc`, which uses Eastern dates.

### Date Range

Full history: **June 2013 to present**. All files in the S3 archive at `https://s3.amazonaws.com/tripdata/`.

File naming conventions on S3:
- **2013–2023**: annual zips — `YYYY-citibike-tripdata.zip` (one zip per year, one CSV per month inside)
- **2024+**: monthly zips — `YYYYMM-citibike-tripdata.zip`
- Jersey City files follow the same patterns but include `JC` in the filename

### Schema

Two schemas exist, split at the 2021 Lyft takeover:

| Era | Key columns |
|-----|-------------|
| **Old (pre-2021)** | `tripduration`, `starttime`, `stoptime`, `start station id/name/lat/lng`, `end station id/name/lat/lng`, `bikeid`, `usertype`, `birth year`, `gender` |
| **New (2021+)** | `ride_id`, `rideable_type`, `started_at`, `ended_at`, `start_station_name/id`, `end_station_name/id`, `start_lat/lng`, `end_lat/lng`, `member_casual` |

Both are normalised to a single unified schema on load (see `etl/citibike_load.py`). Old-schema timestamps (`M/D/YYYY HH:MM:SS`) are rewritten to `YYYY-MM-DD HH:MM:SS` during normalisation.

### Pipeline Stages

```
S3 archive
  → GCS (raw zips or CSVs, for re-processing without re-downloading)
  → BigQuery raw table: citibike.trips  (unified schema, append-only, source_file column)
  → BigQuery clean view: citibike.trips_clean  (dedup, cast, region, Haversine distance)
  → BigQuery daily summary view: citibike.daily_summary  (trip counts, avg distance, Eastern date)
  → BigQuery materialized daily table: citibike.daily  (pre-aggregated, used for viz and weather join)
```

### Validation

Row counts should be cross-checked against two references:
1. **Citibike monthly operating reports**: https://citibikenyc.com/system-data/operating-reports
2. **NYU reference dataset**: `nyu-datasets.citibike` (secondary cross-check)

Flag months where loaded row count differs from operating report by more than 1%.

---

## Cloud Credentials

- **Provider:** GCP
- **Project:** `jf-5530`
- **Service Account:** `claude-agent@jf-5530.iam.gserviceaccount.com`

### Roles Granted

| Role | Reason |
|------|--------|
| `roles/bigquery.dataEditor` | Read `nyu-datasets.weather.m_weather_daily_nyc` and write pipeline output tables |
| `roles/bigquery.jobUser` | Run BigQuery queries and load jobs |
| `roles/run.developer` | Deploy and update the Cloud Run visualization website |

### Authentication

This is a multi-user setup. Each team member has their own encrypted credentials file: `.cloud-credentials.<email>.enc`. The agent authenticates automatically at session start via the SessionStart hook in `.claude/hooks/cloud-auth.sh`.

**Required env var:** `CLOUD_CREDENTIALS_KEY` (or `GCP_CREDENTIALS_KEY`) — set this in Claude Code settings, never commit it.

### Adding a Team Member

Open this repo in Claude Code and say: "Add me as a team member to cloud access." The agent will handle key generation and encryption.

### Escalating Permissions

If a command fails with 403/access denied, say: "Fix cloud permissions" and describe what failed. The agent will identify the missing role and guide you to grant it.
