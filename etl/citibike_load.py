"""
ETL: Download Citibike trip data from S3 and load into BigQuery.

Handles both schemas:
  - New (2021+): ride_id, rideable_type, started_at, ended_at, ...
  - Old (pre-2021): tripduration, starttime, stoptime, start station id, ...

Both are normalised to a single unified schema before loading.

File naming on S3:
  - 2013–2023: annual zips  →  2015-citibike-tripdata.zip   (use --month 2015)
  - 2024+:     monthly zips →  202401-citibike-tripdata.zip  (use --month 202401)

Annual zips contain one CSV per month; all are loaded into the same table.

Usage:
    python etl/citibike_load.py --month 202401   # monthly (2024+)
    python etl/citibike_load.py --month 2015     # annual  (2013-2023)
"""

import argparse
import csv
import io
import os
import subprocess
import zipfile
import tempfile
from datetime import datetime
from pathlib import Path

from google.cloud import bigquery
from google.oauth2 import service_account

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ID = "jf-5530"
DATASET_ID = "citibike"
TABLE_ID   = "trips"
S3_BASE    = "https://s3.amazonaws.com/tripdata"

# ---------------------------------------------------------------------------
# Unified BigQuery schema
# Old-schema-only fields (tripduration, bikeid, usertype, birth_year, gender)
# are included so the same table works for all years.
# ---------------------------------------------------------------------------
SCHEMA = [
    bigquery.SchemaField("ride_id",            "STRING"),
    bigquery.SchemaField("rideable_type",       "STRING"),
    bigquery.SchemaField("started_at",          "TIMESTAMP"),
    bigquery.SchemaField("ended_at",            "TIMESTAMP"),
    bigquery.SchemaField("start_station_name",  "STRING"),
    bigquery.SchemaField("start_station_id",    "STRING"),
    bigquery.SchemaField("end_station_name",    "STRING"),
    bigquery.SchemaField("end_station_id",      "STRING"),
    bigquery.SchemaField("start_lat",           "FLOAT64"),
    bigquery.SchemaField("start_lng",           "FLOAT64"),
    bigquery.SchemaField("end_lat",             "FLOAT64"),
    bigquery.SchemaField("end_lng",             "FLOAT64"),
    bigquery.SchemaField("member_casual",       "STRING"),
    # Old-schema extras (NULL for new-schema rows)
    bigquery.SchemaField("tripduration",        "INTEGER"),
    bigquery.SchemaField("bikeid",              "STRING"),
    bigquery.SchemaField("usertype",            "STRING"),
    bigquery.SchemaField("birth_year",          "STRING"),
    bigquery.SchemaField("gender",              "STRING"),
    # Provenance
    bigquery.SchemaField("source_file",         "STRING"),
]

UNIFIED_COLUMNS = [f.name for f in SCHEMA]

# ---------------------------------------------------------------------------
# Column-name normalisation maps
# ---------------------------------------------------------------------------

# New schema headers → unified names (mostly already match; listed for explicitness)
NEW_SCHEMA_MAP = {
    "ride_id":            "ride_id",
    "rideable_type":      "rideable_type",
    "started_at":         "started_at",
    "ended_at":           "ended_at",
    "start_station_name": "start_station_name",
    "start_station_id":   "start_station_id",
    "end_station_name":   "end_station_name",
    "end_station_id":     "end_station_id",
    "start_lat":          "start_lat",
    "start_lng":          "start_lng",
    "end_lat":            "end_lat",
    "end_lng":            "end_lng",
    "member_casual":      "member_casual",
}

# Old schema headers → unified names
OLD_SCHEMA_MAP = {
    "tripduration":               "tripduration",
    "starttime":                  "started_at",
    "stoptime":                   "ended_at",
    "start station id":           "start_station_id",
    "start station name":         "start_station_name",
    "start station latitude":     "start_lat",
    "start station longitude":    "start_lng",
    "end station id":             "end_station_id",
    "end station name":           "end_station_name",
    "end station latitude":       "end_lat",
    "end station longitude":      "end_lng",
    "bikeid":                     "bikeid",
    "usertype":                   "usertype",
    "birth year":                 "birth_year",
    "gender":                     "gender",
}

# Canonical set of headers that identify the new schema
NEW_SCHEMA_MARKER = "ride_id"


def detect_schema(header_row: list[str]) -> str:
    """Return 'new' or 'old' based on the CSV header."""
    normalised = [h.strip().lower() for h in header_row]
    if NEW_SCHEMA_MARKER in normalised:
        return "new"
    return "old"


# Old schema timestamps: M/D/YYYY HH:MM:SS  →  YYYY-MM-DD HH:MM:SS
_OLD_TS_FORMATS = ["%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"]

def _normalise_timestamp(value: str) -> str:
    """Reformat old-style M/D/YYYY timestamps to YYYY-MM-DD HH:MM:SS."""
    if not value:
        return ""
    for fmt in _OLD_TS_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass
    return value  # already in acceptable format or empty


_TIMESTAMP_COLS = {"started_at", "ended_at"}


def remap_row(row: dict, col_map: dict, source_file: str, is_old: bool) -> dict:
    """Map a raw CSV row to unified column names, filling missing cols with ''."""
    unified = {col: "" for col in UNIFIED_COLUMNS}
    unified["source_file"] = source_file
    for raw_col, value in row.items():
        target = col_map.get(raw_col.strip().lower())
        if target:
            if is_old and target in _TIMESTAMP_COLS:
                value = _normalise_timestamp(value)
            unified[target] = value
    return unified


# ---------------------------------------------------------------------------
# BQ helpers
# ---------------------------------------------------------------------------

def get_bq_client(key_path: str) -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(
        key_path,
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    return bigquery.Client(project=PROJECT_ID, credentials=creds)


def ensure_dataset(client: bigquery.Client):
    dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET_ID}")
    dataset_ref.location = "US"
    client.create_dataset(dataset_ref, exists_ok=True)
    print(f"Dataset `{DATASET_ID}` ready.")


def ensure_table(client: bigquery.Client):
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
    table = bigquery.Table(table_ref, schema=SCHEMA)
    client.create_table(table, exists_ok=True)
    print(f"Table `{DATASET_ID}.{TABLE_ID}` ready.")


# ---------------------------------------------------------------------------
# Download / extract
# ---------------------------------------------------------------------------

def download_and_extract(month: str, tmpdir: str) -> list[tuple[Path, str]]:
    """Download zip and extract all CSVs. Returns [(csv_path, zip_filename), ...]."""
    filename = f"{month}-citibike-tripdata.zip"
    url = f"{S3_BASE}/{filename}"
    zip_path = Path(tmpdir) / filename

    print(f"Downloading {url} ...")
    subprocess.run(["curl", "-fsSL", "-o", str(zip_path), url], check=True)
    size_mb = zip_path.stat().st_size / 1_000_000
    print(f"Downloaded {size_mb:.1f} MB.")

    print("Extracting CSV(s)...")
    results = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        csv_names = sorted(
            n for n in zf.namelist()
            if n.endswith(".csv") and not n.startswith("__MACOSX")
        )
        if not csv_names:
            raise ValueError(f"No CSV found in {filename}")
        for csv_name in csv_names:
            zf.extract(csv_name, tmpdir)
            csv_path = Path(tmpdir) / csv_name
            print(f"  {csv_name} ({csv_path.stat().st_size / 1_000_000:.1f} MB)")
            results.append((csv_path, filename))
    return results


# ---------------------------------------------------------------------------
# Normalise + load
# ---------------------------------------------------------------------------

def normalise_and_load(client: bigquery.Client, csv_path: Path, source_file: str):
    """Detect schema, remap columns, stream normalised CSV into BigQuery."""
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

    # Peek at header to detect schema
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []

    schema_version = detect_schema(header)
    is_old = schema_version == "old"
    col_map = NEW_SCHEMA_MAP if not is_old else OLD_SCHEMA_MAP
    print(f"Detected schema: {schema_version}  (sample headers: {header[:4]})")

    # Rewrite to a normalised in-memory CSV buffer
    print("Normalising columns...")
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=UNIFIED_COLUMNS)
    writer.writeheader()

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            writer.writerow(remap_row(row, col_map, source_file, is_old))

    buf.seek(0)
    data_bytes = buf.getvalue().encode("utf-8")
    print(f"Normalised buffer: {len(data_bytes) / 1_000_000:.1f} MB")

    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA,
        source_format=bigquery.SourceFormat.CSV,
        skip_leading_rows=1,
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
        allow_jagged_rows=True,
        ignore_unknown_values=True,
        null_marker="",
    )

    print(f"Loading into BigQuery `{table_ref}` ...")
    job = client.load_table_from_file(
        io.BytesIO(data_bytes), table_ref, job_config=job_config
    )
    job.result()
    print(f"Load job complete: {job.job_id}")


# ---------------------------------------------------------------------------
# Row count
# ---------------------------------------------------------------------------

def row_count(client: bigquery.Client) -> int:
    result = client.query(
        f"SELECT COUNT(*) AS n FROM `{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}`"
    ).result()
    return list(result)[0].n


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default="202401", help="e.g. 202401 or 201501")
    parser.add_argument("--key", default="/tmp/gcp-adc-credentials.json",
                        help="Path to service account key JSON")
    args = parser.parse_args()

    if not os.path.exists(args.key):
        raise FileNotFoundError(
            f"Service account key not found at {args.key}. "
            "Decrypt credentials first."
        )

    client = get_bq_client(args.key)
    ensure_dataset(client)
    ensure_table(client)

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_files = download_and_extract(args.month, tmpdir)
        for csv_path, source_file in csv_files:
            normalise_and_load(client, csv_path, source_file)

    count = row_count(client)
    print(f"\nRow count in `{DATASET_ID}.{TABLE_ID}`: {count:,}")


if __name__ == "__main__":
    main()
