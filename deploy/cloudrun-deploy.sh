#!/usr/bin/env bash
# Build the Dockerfile with Cloud Build and deploy it to Cloud Run.
#
# Usage (from the repo root):
#   GCP_PROJECT=my-project ./deploy/cloudrun-deploy.sh
#
# Run database migrations first if there are new ones:
#   cd src && ../.venv/bin/alembic upgrade head
set -euo pipefail

: "${GCP_PROJECT:?Set GCP_PROJECT to your Google Cloud project id}"
REGION=${REGION:-us-east1}           # free-tier Cloud Storage region, near Supabase (us-east)
SERVICE=${SERVICE:-marketplace-api}
# 0 = free tier with cold starts after idle; 1 = always warm (billed).
MIN_INSTANCES=${MIN_INSTANCES:-0}

cd "$(dirname "$0")/.."

SECRETS="DATABASE_URL=database-url:latest"
if gcloud secrets describe sentry-dsn --project "$GCP_PROJECT" >/dev/null 2>&1; then
  SECRETS="$SECRETS,SENTRY_DSN=sentry-dsn:latest"
fi

# --max-instances 1: rate-limit counters are kept in memory, so they're only
# accurate with a single instance.
gcloud run deploy "$SERVICE" \
  --project "$GCP_PROJECT" \
  --region "$REGION" \
  --source . \
  --allow-unauthenticated \
  --min-instances "$MIN_INSTANCES" \
  --max-instances 1 \
  --cpu 1 \
  --memory 512Mi \
  --concurrency 80 \
  --timeout 60 \
  --env-vars-file deploy/cloudrun.env.yaml \
  --set-secrets "$SECRETS"

# Each deploy stores a ~200 MB image; keep only recent ones so storage stays
# inside Artifact Registry's free 0.5 GB (it would otherwise count toward the
# budget). The repository is created by the first --source deploy.
gcloud artifacts repositories set-cleanup-policies cloud-run-source-deploy \
  --project "$GCP_PROJECT" --location "$REGION" \
  --policy deploy/artifact-cleanup.json --no-dry-run >/dev/null

URL=$(gcloud run services describe "$SERVICE" --project "$GCP_PROJECT" --region "$REGION" --format='value(status.url)')
echo "Deployed: $URL"
curl -fsS "$URL/health" && echo
