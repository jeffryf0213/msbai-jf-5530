#!/bin/bash
# Deploy the Citibike dashboard to Cloud Run.
#
# Prerequisites (run once in Cloud Shell):
#   gcloud services enable run.googleapis.com cloudbuild.googleapis.com
#   gcloud projects add-iam-policy-binding jf-5530 \
#     --member="serviceAccount:claude-agent@jf-5530.iam.gserviceaccount.com" \
#     --role="roles/run.developer"
#
# Usage:
#   bash dashboard/deploy.sh

set -euo pipefail

PROJECT=jf-5530
SERVICE=citibike-dashboard
REGION=us-east1
IMAGE="gcr.io/${PROJECT}/${SERVICE}"

echo "=== Building and pushing Docker image ==="
cd "$(dirname "$0")"
gcloud builds submit --tag "${IMAGE}" --project "${PROJECT}" .

echo ""
echo "=== Deploying to Cloud Run ==="
gcloud run deploy "${SERVICE}" \
  --image "${IMAGE}" \
  --platform managed \
  --region "${REGION}" \
  --project "${PROJECT}" \
  --allow-unauthenticated \
  --service-account "claude-agent@${PROJECT}.iam.gserviceaccount.com" \
  --memory 512Mi \
  --cpu 1 \
  --min-instances 0 \
  --max-instances 3 \
  --port 8080

echo ""
echo "=== Deployed ==="
gcloud run services describe "${SERVICE}" \
  --platform managed \
  --region "${REGION}" \
  --project "${PROJECT}" \
  --format "value(status.url)"
