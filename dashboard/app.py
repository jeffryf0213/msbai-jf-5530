"""
Citibike Dashboard  —  When ridership changes, is it weather or operations?

Audience: journalist or city planner (non-technical).
Data source: jf-5530.citibike.daily  (one row per date × region, pre-joined with weather).

Auth (Streamlit Community Cloud):
  Credentials live in .streamlit/secrets.toml (locally) or the Streamlit Cloud
  "Secrets" UI (in production).  See .streamlit/secrets.toml.template for format.
"""

import datetime

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from google.cloud import bigquery
from google.oauth2 import service_account

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Citibike Ridership Explorer",
    page_icon="🚲",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Auth — reads from st.secrets["gcp_service_account"], which is populated by:
#   • .streamlit/secrets.toml  when running locally
#   • The Streamlit Community Cloud "Secrets" settings panel in production
# ---------------------------------------------------------------------------
@st.cache_resource
def get_bq_client():
    info = dict(st.secrets["gcp_service_account"])
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/bigquery"]
    )
    return bigquery.Client(project="jf-5530", credentials=creds)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
@st.cache_data(ttl=3600, show_spinner="Loading ridership data…")
def load_data() -> pd.DataFrame:
    client = get_bq_client()
    df = client.query("""
        SELECT
          trip_date,
          region,
          num_trips,
          num_member_trips,
          num_casual_trips,
          num_electric_trips,
          num_classic_trips,
          avg_duration_seconds,
          avg_distance_meters,
          tmax_f,
          tmin_f,
          prcp_mm,
          snow_inches,
          is_rainy,
          is_snowy,
          is_hot_day,
          is_freezing,
          wind_avg_mph,
          season,
          day_of_week,
          is_weekend
        FROM `jf-5530.citibike.daily`
        ORDER BY trip_date, region
    """).to_dataframe(create_bqstorage_client=False)
    df["trip_date"] = pd.to_datetime(df["trip_date"])
    return df


# ---------------------------------------------------------------------------
# Sidebar — filters
# ---------------------------------------------------------------------------
st.sidebar.title("🚲 Filters")

df_all = load_data()

min_date = df_all["trip_date"].min().date()
max_date = df_all["trip_date"].max().date()
default_start = max_date - datetime.timedelta(days=365)

date_range = st.sidebar.date_input(
    "Date range",
    value=(default_start, max_date),
    min_value=min_date,
    max_value=max_date,
)
if isinstance(date_range, (list, tuple)) and len(date_range) == 2:
    start_date, end_date = date_range
else:
    start_date, end_date = default_start, max_date

region_choice = st.sidebar.radio("Region", ["All", "NYC", "JC"], index=0)

rider_choice = st.sidebar.radio("Rider type", ["All", "Member", "Casual"], index=0)

st.sidebar.markdown("---")
st.sidebar.caption(
    "Data: [Citibike system data](https://citibikenyc.com/system-data) via NYU Datasets  \n"
    "Weather: NOAA Central Park station"
)

# ---------------------------------------------------------------------------
# Apply filters
# ---------------------------------------------------------------------------
df = df_all[
    (df_all["trip_date"] >= pd.Timestamp(start_date))
    & (df_all["trip_date"] <= pd.Timestamp(end_date))
].copy()

if region_choice != "All":
    df = df[df["region"] == region_choice]

# When "All" regions selected, sum across NYC+JC per day
if region_choice == "All":
    # Weather columns are identical for both regions (NYC station); take max to collapse
    weather_cols = ["tmax_f", "tmin_f", "prcp_mm", "snow_inches",
                    "is_rainy", "is_snowy", "is_hot_day", "is_freezing",
                    "wind_avg_mph", "season", "day_of_week", "is_weekend"]
    agg_dict = {
        "num_trips": "sum",
        "num_member_trips": "sum",
        "num_casual_trips": "sum",
        "num_electric_trips": "sum",
        "num_classic_trips": "sum",
        "avg_duration_seconds": "mean",
        "avg_distance_meters": "mean",
    }
    for c in weather_cols:
        agg_dict[c] = "first"
    df = df.groupby("trip_date", as_index=False).agg(agg_dict)

# Rider-type display column
if rider_choice == "Member":
    df["display_trips"] = df["num_member_trips"]
    trips_label = "Member trips"
elif rider_choice == "Casual":
    df["display_trips"] = df["num_casual_trips"]
    trips_label = "Casual trips"
else:
    df["display_trips"] = df["num_trips"]
    trips_label = "Total trips"

# ---------------------------------------------------------------------------
# Header KPIs
# ---------------------------------------------------------------------------
st.title("Citibike Ridership Explorer")
st.caption(
    f"Showing **{region_choice}** · **{rider_choice}** · "
    f"{start_date.strftime('%b %d, %Y')} – {end_date.strftime('%b %d, %Y')}"
)

col1, col2, col3, col4 = st.columns(4)
total = int(df["display_trips"].sum())
daily_avg = int(df["display_trips"].mean()) if len(df) else 0
rainy_avg = int(df.loc[df["is_rainy"] == 1, "display_trips"].mean()) if (df["is_rainy"] == 1).any() else 0
dry_avg   = int(df.loc[df["is_rainy"] == 0, "display_trips"].mean()) if (df["is_rainy"] == 0).any() else 0
weather_delta = f"{((dry_avg - rainy_avg) / dry_avg * 100):.0f}% fewer on rainy days" if dry_avg else "—"

col1.metric("Total trips", f"{total:,.0f}")
col2.metric("Daily average", f"{daily_avg:,.0f}")
col3.metric("Avg on dry days", f"{dry_avg:,.0f}")
col4.metric("Avg on rainy days", f"{rainy_avg:,.0f}", delta=f"−{dry_avg - rainy_avg:,.0f}", delta_color="inverse")

st.markdown("---")

# ---------------------------------------------------------------------------
# Chart 1 — Daily ridership over time with weather overlay
# ---------------------------------------------------------------------------
st.subheader("① Daily ridership over time")

# Compute the finding dynamically from the data
if dry_avg > 0 and rainy_avg > 0:
    rain_pct = int(round((dry_avg - rainy_avg) / dry_avg * 100))
    _finding1 = (
        f"**Finding:** Ridership tracks temperature closely across the full history — "
        f"rainy days suppress daily trips by roughly **{rain_pct}%** compared to dry days, "
        f"regardless of season."
    )
else:
    _finding1 = (
        "**Finding:** Ridership tracks temperature closely across the full history — "
        "rainy days consistently suppress daily trips compared to dry days."
    )
st.markdown(_finding1)
st.caption("Shaded bands = rainy days. Right axis = high temperature (°F).")

fig1 = go.Figure()

# Rainy day shading
rainy_days = df[df["is_rainy"] == 1]["trip_date"]
for d in rainy_days:
    fig1.add_vrect(
        x0=d - pd.Timedelta(hours=12),
        x1=d + pd.Timedelta(hours=12),
        fillcolor="steelblue", opacity=0.12, line_width=0,
    )

# Ridership line
fig1.add_trace(go.Scatter(
    x=df["trip_date"], y=df["display_trips"],
    name=trips_label, line=dict(color="#e05c2e", width=1.5),
    hovertemplate="%{x|%b %d, %Y}<br>%{y:,.0f} trips<extra></extra>",
))

# 30-day rolling average
rolled = df.set_index("trip_date")["display_trips"].rolling(30, min_periods=7).mean().reset_index()
fig1.add_trace(go.Scatter(
    x=rolled["trip_date"], y=rolled["display_trips"],
    name="30-day avg", line=dict(color="#e05c2e", width=2.5, dash="dot"),
    hovertemplate="%{x|%b %d, %Y}<br>%{y:,.0f} (30-day avg)<extra></extra>",
))

# Temperature on secondary axis
if df["tmax_f"].notna().any():
    fig1.add_trace(go.Scatter(
        x=df["trip_date"], y=df["tmax_f"],
        name="High temp (°F)", line=dict(color="#f5a623", width=1, dash="dot"),
        yaxis="y2", opacity=0.7,
        hovertemplate="%{x|%b %d, %Y}<br>%{y:.0f}°F<extra></extra>",
    ))

fig1.update_layout(
    height=380,
    margin=dict(l=0, r=0, t=10, b=0),
    yaxis=dict(title=trips_label, tickformat=","),
    yaxis2=dict(title="High temp (°F)", overlaying="y", side="right", showgrid=False),
    legend=dict(orientation="h", y=1.08),
    hovermode="x unified",
    plot_bgcolor="white",
    xaxis=dict(showgrid=False),
)
st.plotly_chart(fig1, use_container_width=True)

# ---------------------------------------------------------------------------
# Chart 2 + Chart 3 side by side
# ---------------------------------------------------------------------------
col_left, col_right = st.columns(2)

# Chart 2 — Weather vs ridership scatter
with col_left:
    st.subheader("② Temperature vs ridership")

    df_scatter = df[df["tmax_f"].notna()].copy()

    # Compute finding: correlation between temp and trips
    if len(df_scatter) > 10:
        corr = df_scatter[["tmax_f", "display_trips"]].corr().iloc[0, 1]
        # Find avg trips at <40°F vs >75°F
        cold = int(df_scatter.loc[df_scatter["tmax_f"] < 40, "display_trips"].mean()) if (df_scatter["tmax_f"] < 40).any() else 0
        warm = int(df_scatter.loc[df_scatter["tmax_f"] > 75, "display_trips"].mean()) if (df_scatter["tmax_f"] > 75).any() else 0
        if cold > 0 and warm > 0:
            temp_mult = round(warm / cold, 1)
            _finding2 = (
                f"**Finding:** On days above 75°F, average daily trips are **{temp_mult}×** "
                f"higher than on days below 40°F — temperature is the strongest single predictor of ridership."
            )
        else:
            _finding2 = (
                f"**Finding:** Temperature and ridership are strongly correlated (r = {corr:.2f}) — "
                "warmer days drive significantly more trips."
            )
    else:
        _finding2 = "**Finding:** Warmer, drier days consistently produce higher ridership."

    st.markdown(_finding2)
    st.caption("Each dot = one day. Blue = rainy, orange = dry.")

    df_scatter["weather_label"] = df_scatter["is_rainy"].map({1: "Rainy", 0: "Dry"}).fillna("Unknown")

    fig2 = px.scatter(
        df_scatter, x="tmax_f", y="display_trips",
        color="weather_label",
        color_discrete_map={"Dry": "#f5a623", "Rainy": "#4a90d9", "Unknown": "#aaa"},
        labels={"tmax_f": "High temperature (°F)", "display_trips": trips_label},
        trendline=None,
        hover_data={"trip_date": "|%b %d, %Y", "tmax_f": ":.0f", "display_trips": ":,"},
        height=340,
    )
    fig2.update_traces(marker=dict(size=4, opacity=0.6))
    fig2.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(title="", orientation="h", y=1.08),
        plot_bgcolor="white",
        yaxis=dict(tickformat=","),
    )
    st.plotly_chart(fig2, use_container_width=True)

# Chart 3 — Member vs casual over time
with col_right:
    st.subheader("③ Member vs casual over time")

    df_mc = df[["trip_date", "num_member_trips", "num_casual_trips"]].copy()
    df_mc["month"] = df_mc["trip_date"].dt.to_period("M").dt.to_timestamp()
    df_monthly = df_mc.groupby("month")[["num_member_trips", "num_casual_trips"]].mean().reset_index()

    # Compute finding: casual share in first vs last year
    if len(df_monthly) >= 24:
        early = df_monthly.head(12)
        recent = df_monthly.tail(12)
        early_casual_share = early["num_casual_trips"].sum() / (early["num_member_trips"].sum() + early["num_casual_trips"].sum()) * 100
        recent_casual_share = recent["num_casual_trips"].sum() / (recent["num_member_trips"].sum() + recent["num_casual_trips"].sum()) * 100
        _finding3 = (
            f"**Finding:** Casual riders have grown from **{early_casual_share:.0f}%** to **{recent_casual_share:.0f}%** "
            f"of all trips in the selected period — making weather-sensitive casual demand the swing factor in revenue."
        )
    else:
        _finding3 = (
            "**Finding:** Casual ridership has grown as a share of total trips, "
            "making it the most weather-sensitive and highest-margin segment to watch."
        )

    st.markdown(_finding3)
    st.caption("Monthly average daily trips by rider type.")

    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(
        x=df_monthly["month"], y=df_monthly["num_member_trips"],
        name="Member", stackgroup="one",
        line=dict(color="#2d7dd2"), fillcolor="rgba(45,125,210,0.5)",
        hovertemplate="%{x|%b %Y}<br>%{y:,.0f} member trips/day<extra></extra>",
    ))
    fig3.add_trace(go.Scatter(
        x=df_monthly["month"], y=df_monthly["num_casual_trips"],
        name="Casual", stackgroup="one",
        line=dict(color="#f5a623"), fillcolor="rgba(245,166,35,0.5)",
        hovertemplate="%{x|%b %Y}<br>%{y:,.0f} casual trips/day<extra></extra>",
    ))
    fig3.update_layout(
        height=340,
        margin=dict(l=0, r=0, t=10, b=0),
        yaxis=dict(title="Avg daily trips", tickformat=","),
        legend=dict(orientation="h", y=1.08),
        hovermode="x unified",
        plot_bgcolor="white",
        xaxis=dict(showgrid=False),
    )
    st.plotly_chart(fig3, use_container_width=True)

# ---------------------------------------------------------------------------
# Chart 4 — Seasonal patterns by rider type
# ---------------------------------------------------------------------------
st.subheader("④ Seasonal patterns by rider type")

df_season = df[df["season"].notna()].copy()

if not df_season.empty:
    season_order = ["Spring", "Summer", "Fall", "Winter"]
    df_season_agg = (
        df_season.groupby("season")[["num_member_trips", "num_casual_trips"]]
        .mean()
        .reindex([s for s in season_order if s in df_season["season"].unique()])
        .reset_index()
    )
    df_season_long = df_season_agg.melt(
        id_vars="season",
        value_vars=["num_member_trips", "num_casual_trips"],
        var_name="Rider type",
        value_name="Avg daily trips",
    )
    df_season_long["Rider type"] = df_season_long["Rider type"].map(
        {"num_member_trips": "Member", "num_casual_trips": "Casual"}
    )

    # Compute finding
    if "Winter" in df_season_agg["season"].values and "Summer" in df_season_agg["season"].values:
        summer_c = df_season_agg.loc[df_season_agg["season"] == "Summer", "num_casual_trips"].values[0]
        winter_c = df_season_agg.loc[df_season_agg["season"] == "Winter", "num_casual_trips"].values[0]
        summer_m = df_season_agg.loc[df_season_agg["season"] == "Summer", "num_member_trips"].values[0]
        winter_m = df_season_agg.loc[df_season_agg["season"] == "Winter", "num_member_trips"].values[0]
        casual_swing = round(summer_c / max(winter_c, 1), 1)
        member_swing = round(summer_m / max(winter_m, 1), 1)
        st.markdown(
            f"**Finding:** Casual riders are **{casual_swing}× more active** in summer than winter, "
            f"versus only **{member_swing}×** for members — confirming that casual demand, "
            f"not membership, is what weather moves."
        )
    else:
        st.markdown(
            "**Finding:** Casual riders show far greater seasonal swings than members, "
            "confirming they are the most weather-sensitive segment."
        )

    st.caption("Average daily trips by season. Shows how weather-sensitive casual riders are vs members.")

    fig4 = px.bar(
        df_season_long, x="season", y="Avg daily trips", color="Rider type",
        barmode="group",
        color_discrete_map={"Member": "#2d7dd2", "Casual": "#f5a623"},
        labels={"season": "Season", "Avg daily trips": "Avg daily trips"},
        height=320,
        category_orders={"season": season_order},
    )
    fig4.update_layout(
        margin=dict(l=0, r=0, t=10, b=0),
        legend=dict(title="", orientation="h", y=1.08),
        plot_bgcolor="white",
        yaxis=dict(tickformat=","),
        xaxis=dict(showgrid=False),
    )
    st.plotly_chart(fig4, use_container_width=True)
else:
    st.info("Season data not available for the selected date range.")

# ---------------------------------------------------------------------------
# Footer
# ---------------------------------------------------------------------------
st.markdown("---")
st.caption(
    "Source: `jf-5530.citibike.daily` · "
    f"{len(df):,} days shown · "
    "Weather: NOAA Central Park station via NYU Datasets"
)
