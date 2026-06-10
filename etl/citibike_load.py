"""
ETL: Download Citibike trip data from S3 and load into BigQuery.

Usage:
    python etl/citibike_load.py --month 202401
"""

import argparse
import os
import subprocess
import zipfile
import tempfile
from pathlib import Path

from google.cloud import bigquery
from google.oauth2 import service_account

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
PROJECT_ID = "jf-5530"
DATASET_ID = "citibike"
TABLE_ID = "trips"
S3_BASE = "https://s3.amazonaws.com/tripdata"

SCHEMA = [
    bigquery.SchemaField("ride_id",             "STRING"),
    bigquery.SchemaField("rideable_type",        "STRING"),
    bigquery.SchemaField("started_at",           "TIMESTAMP"),
    bigquery.SchemaField("ended_at",             "TIMESTAMP"),
    bigquery.SchemaField("start_station_name",   "STRING"),
    bigquery.SchemaField("start_station_id",     "STRING"),
    bigquery.SchemaField("end_station_name",     "STRING"),
    bigquery.SchemaField("end_station_id",       "STRING"),
    bigquery.SchemaField("start_lat",            "FLOAT64"),
    bigquery.SchemaField("start_lng",            "FLOAT64"),
    bigquery.SchemaField("end_lat",              "FLOAT64"),
    bigquery.SchemaField("end_lng",              "FLOAT64"),
    bigquery.SchemaField("member_casual",        "STRING"),
]


def get_bq_client(key_path: str) -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(
        key_path,
        scopes=["https://www.googleapis.com/auth/bigquery"],
    )
    return bigquery.Client(project=PROJECT_ID, credentials=creds)


def ensure_dataset(client: bigquery.Client):
    dataset_ref = bigquery.Dataset(f"{PROJECT_ID}.{DATASET_ID}")
    dataset_ref.location = "US"
    try:
        client.create_dataset(dataset_ref, exists_ok=True)
        print(f"Dataset `{DATASET_ID}` ready.")
    except Exception as e:
        print(f"Dataset creation skipped: {e}")


def ensure_table(client: bigquery.Client):
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"
    table = bigquery.Table(table_ref, schema=SCHEMA)
    try:
        client.create_table(table, exists_ok=True)
        print(f"Table `{DATASET_ID}.{TABLE_ID}` ready.")
    except Exception as e:
        print(f"Table creation skipped: {e}")


def download_and_extract(month: str, tmpdir: str) -> Path:
    filename = f"{month}-citibike-tripdata.zip"
    url = f"{S3_BASE}/{filename}"
    zip_path = Path(tmpdir) / filename

    print(f"Downloading {url} ...")
    subprocess.run(["curl", "-fsSL", "-o", str(zip_path), url], check=True)
    size_mb = zip_path.stat().st_size / 1_000_000
    print(f"Downloaded {size_mb:.1f} MB.")

    print("Extracting CSV...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        csv_files = [n for n in zf.namelist() if n.endswith(".csv")]
        if not csv_files:
            raise ValueError(f"No CSV found in {filename}")
        csv_name = csv_files[0]
        zf.extract(csv_name, tmpdir)
        csv_path = Path(tmpdir) / csv_name
    print(f"Extracted: {csv_name} ({csv_path.stat().st_size / 1_000_000:.1f} MB)")
    return csv_path


def load_csv_to_bq(client: bigquery.Client, csv_path: Path, month: str):
    table_ref = f"{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}"

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
    with open(csv_path, "rb") as f:
        job = client.load_table_from_file(f, table_ref, job_config=job_config)

    job.result()  # wait for completion
    print(f"Load job complete: {job.job_id}")


def row_count(client: bigquery.Client) -> int:
    result = client.query(
        f"SELECT COUNT(*) AS n FROM `{PROJECT_ID}.{DATASET_ID}.{TABLE_ID}`"
    ).result()
    return list(result)[0].n


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--month", default="202401", help="e.g. 202401")
    parser.add_argument("--key", default="/tmp/gcp-adc-credentials.json",
                        help="Path to service account key JSON")
    args = parser.parse_args()

    if not os.path.exists(args.key):
        raise FileNotFoundError(
            f"Service account key not found at {args.key}. "
            "Run cloud-auth.sh or decrypt credentials first."
        )

    client = get_bq_client(args.key)
    ensure_dataset(client)
    ensure_table(client)

    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = download_and_extract(args.month, tmpdir)
        load_csv_to_bq(client, csv_path, args.month)

    count = row_count(client)
    print(f"\nRow count in `{DATASET_ID}.{TABLE_ID}`: {count:,}")


if __name__ == "__main__":
    main()
