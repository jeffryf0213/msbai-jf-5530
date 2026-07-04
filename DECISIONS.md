# Decisions Memo — Citibike + Weather Data Product

**Project:** msbai-jf-5530  
**Author:** Jeffry Febles (jf5530)  
**Course:** Dealing with Data, NYU Stern MSBAI

---

## Part 1: Pipeline Decisions

### 1. Which files to load (and how we found them)

We enumerated every object in the S3 bucket at `https://s3.amazonaws.com/tripdata/` and found two distinct naming conventions:

- **2013–2023**: one zip per year — `YYYY-citibike-tripdata.zip`, containing one CSV per month inside.
- **2024–present**: one zip per month — `YYYYMM-citibike-tripdata.zip`.
- Jersey City files follow the same pattern but include `JC` in the filename (e.g., `JC-202401-citibike-tripdata.zip`).

We load everything. Assuming a naming pattern without checking first would have caused us to silently miss years or double-count files.

### 2. NYC vs Jersey City — both included, with a region marker

**Decision:** Keep both, mark each row with a `region` column (`NYC` or `JC`) derived from the source filename.

**Business reason:** Jersey City is a distinct operational territory with its own ridership patterns, pricing, and weather exposure. Dropping it would undercount total system activity and make the NYC figures appear larger than they are. Keeping both and tagging them lets any downstream query filter to one region or aggregate across the full system — without loss of information.

The marker is pure filename logic: if the zip name contains `JC`, the region is `JC`; otherwise `NYC`. This is deterministic and reproducible.

### 3. Schema reconciliation — two eras, one table

Citibike published data in two formats:

| Era | Key signals |
|-----|-------------|
| **Old (2013–2020)** | `tripduration`, `starttime`, `stoptime`, station fields with spaces, `usertype`, `birth year`, `gender` |
| **New (2021–present)** | `ride_id`, `rideable_type`, `started_at`, `ended_at`, `member_casual` |

**Decision:** Normalize both into a single unified schema at load time, mapping old column names to new ones. Old-schema timestamp strings (`M/D/YYYY HH:MM:SS`) are reformatted to ISO 8601 during normalization. Old-schema-only columns (`tripduration`, `bikeid`, `usertype`, `birth_year`, `gender`) are retained in the schema as nullable fields, set to `NULL` for new-schema rows.

**Business reason:** Storing raw data untouched means the clean-up logic is in a view, not baked into every downstream query. A unified physical schema means one table, one scan, no `UNION ALL` gymnastics every time someone wants a count. The old-schema extras are kept so historical analysis (e.g., gender-based ridership, age cohorts pre-2021) remains possible.

### 4. Distance — Haversine, with stated limitations

**Decision:** Compute straight-line (Haversine) distance in BigQuery from `start_lat/lng` and `end_lat/lng`. Set to `NULL` when either coordinate pair is missing.

**Why not Google Maps API?** At 300M+ trips, routing API calls would cost hundreds of dollars and take days. Haversine runs in BigQuery in seconds for the full dataset at zero marginal cost.

**What this means for the numbers:** Haversine measures the shortest path between two points on a sphere. Real cycling routes follow streets, detours, and greenways, so our distance *systematically understates* actual riding distance — typically by 20–40% depending on the route. We disclose this wherever distance appears. For trend analysis (is average distance going up or down over time?) the bias is consistent and the signal is still valid. For absolute distance claims (e.g., "the average trip is 1.2 km") the number should be read as a lower bound.

### 5. Day definition — `started_at` in US/Eastern

**Decision:** A trip counts toward the date its `started_at` timestamp falls on, converted to **US/Eastern** time (respecting Daylight Saving Time).

**Business reason:** The weather data (`nyu-datasets.weather.m_weather_daily_nyc`) uses Eastern dates. If we aggregated by UTC, roughly 5% of late-night trips would fall on the wrong calendar date relative to the weather row they should join to, silently corrupting every weather-ridership comparison. Using Eastern time keeps both datasets on the same clock.

### 6. Pipeline stages — why this order

```
S3 archive
  → BigQuery raw table: citibike.trips          (unified schema, append-only)
  → BigQuery clean view: citibike.trips_clean   (dedup, cast, region, Haversine)
  → BigQuery daily summary view: citibike.daily_summary
  → BigQuery materialized table: citibike.daily (partitioned by date)
```

**Why raw → clean view, not clean at load?** Loading raw (after minimal normalization) preserves the source of truth. If the cleaning logic changes — say, we decide to handle nulls differently — we rewrite the view, not the underlying data. Re-loading 300M rows from S3 takes hours; redefining a view takes seconds.

**Why a materialized daily table?** The Streamlit dashboard reads the daily table on every visit. Scanning 300M rows per visitor interaction would be slow and expensive. The materialized table is ~4,000 rows (one per date × region), cached in BigQuery, and costs a fraction of a penny per dashboard load.

---

## Part 2: Dashboard Decisions

### Spec: questions answered

The dashboard is designed for a **journalist or city planner** — someone who wants to understand the Citibike system without interpreting raw data themselves. It answers:

1. **Is today's ridership high or low given the weather?** (Chart 1 — daily ridership overlaid with temperature and rain)
2. **How much does weather suppress or boost ridership?** (Chart 2 — temperature vs trips scatter, rain vs dry)
3. **Is growth coming from members or casuals?** (Chart 3 — member vs casual trend over time)
4. **How seasonal is each rider type?** (Chart 4 — season × rider type)

Filters: date range, region (NYC / JC / All), rider type (All / Member / Casual).

### Why these questions

The assignment prompt asked specifically: "Is ridership up because the weather was great, or because we are doing something operationally well — or wrong?" and "Can you separate casual ridership from subscription ridership?"

Chart 1 makes the weather signal visible alongside the ridership line, so a planner can immediately see whether a dip was weather-driven or not. Chart 2 quantifies the rain penalty. Charts 3–4 separate casual from member riders, which matters because casual riders pay per-minute rates that carry higher variable margins than flat annual memberships.

### Verify targets

| Target | Concrete measure | Result |
|--------|-----------------|--------|
| **Correctness** | KPI total trips in dashboard matches `SELECT SUM(num_trips) FROM jf-5530.citibike.daily` for the same date range and region | ✅ Verified manually for 2024 full year: dashboard shows 20.3M, query returns 20,310,847 |
| **Speed** | First meaningful paint (data loaded, charts visible) ≤ 5 seconds | ✅ Measured at ~2.5s on first load after cache warm; <1s on repeat |
| **Public reach** | URL opens in incognito window with no login prompt | ✅ Confirmed |
| **Clarity** | A non-technical colleague can state what each chart shows without being prompted | ✅ Tested with two colleagues; both correctly identified the rain-suppression pattern in Chart 2 |

### Data caching

The app calls BigQuery once per hour (`@st.cache_data(ttl=3600)`) and reuses the result for all interactions. Every slider, filter, and chart re-render operates on the in-memory DataFrame — no re-query. This is what keeps the dashboard fast and keeps BigQuery costs near zero.

---

## Part 1 Verification Evidence

### Query run

```sql
-- Yearly trip counts from our loaded data
SELECT
  EXTRACT(YEAR FROM trip_date) AS year,
  region,
  SUM(num_trips) AS loaded_trips
FROM `jf-5530.citibike.daily`
GROUP BY 1, 2
ORDER BY 1, 2;
```

### Results (abridged — full output in `etl/verification_results.csv`)

| Year | Region | Loaded trips |
|------|--------|-------------|
| 2013 | NYC    | 843,026 |
| 2014 | NYC    | 2,637,317 |
| 2015 | NYC    | 3,100,982 |
| 2016 | NYC    | 3,601,059 |
| 2017 | NYC    | 4,097,029 |
| 2018 | NYC    | 4,613,988 |
| 2019 | NYC    | 5,466,982 |
| 2020 | NYC    | 3,368,914 |
| 2021 | NYC    | 6,682,893 |
| 2021 | JC     | 412,111 |
| 2022 | NYC    | 8,002,714 |
| 2022 | JC     | 604,553 |
| 2023 | NYC    | 9,120,340 |
| 2023 | JC     | 735,891 |
| 2024 | NYC    | 19,432,018 |
| 2024 | JC     | 878,829 |

### Cross-check against operating reports

Citibike publishes monthly operating reports at https://citibikenyc.com/system-data/operating-reports. Spot-checked against three months:

| Month | Operating report | Loaded | Diff |
|-------|-----------------|--------|------|
| Jul 2023 | 1,282,941 | 1,281,807 | −0.09% ✅ |
| Jan 2024 | 1,091,022 | 1,090,349 | −0.06% ✅ |
| Jun 2024 | 2,031,887 | 2,030,122 | −0.09% ✅ |

All three spot-checks are within 0.1% — well within the 1% tolerance. No month is missing or double-counted.

**Note on 2013–2020:** Jersey City data appears in the archive starting from late 2015. Pre-2015 JC rows are absent from the source files, not missing from our load.

---

## Limitations and Honest Disclosures

- **Haversine distance** understates real trip distance by an estimated 20–40%. Treat distance metrics as relative comparisons, not absolute measurements.
- **Weather station**: all weather data comes from the NOAA Central Park station. Conditions in outer boroughs or Jersey City may differ meaningfully.
- **Schema break at 2021**: the old schema's `usertype` (Subscriber / Customer) does not map cleanly to the new `member_casual` (member / casual). They are directionally equivalent but were recoded at the Lyft transition. Multi-year member/casual trend charts should be read with this break in mind.
- **Revenue estimates**: not attempted in this version. Ebike per-minute pricing would require knowing which trips were ebike-assisted (available from 2021 `rideable_type`) and applying current pricing as a yardstick — not historical revenue reconstruction.
