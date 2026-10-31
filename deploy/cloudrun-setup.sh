#!/usr/bin/env bash
# One-time Google Cloud setup for the Cloud Run deployment.
#
# Usage (from the repo root, with gcloud installed and logged in):
#   set -a; source src/.env; set +a        # provides DATABASE_URL, SENTRY_DSN, the Stripe keys and the storage key
#   GCP_PROJECT=my-project ./deploy/cloudrun-setup.sh
#
# Creates (or updates):
#   * the Artifact Registry repository for API images, with a cleanup policy
#   * the API's own runtime service account, which can read only its secrets
#     (instead of the default compute account, which has Editor on the project)
#   * the database-url, sentry-dsn, stripe-secret-key, stripe-webhook-secret,
#     storage-secret-access-key and resend-api-key secrets
#
# Safe to re-run: existing secrets get a new version instead of failing.
set -euo pipefail

: "${GCP_PROJECT:?Set GCP_PROJECT to your Google Cloud project id}"
: "${DATABASE_URL:?Set DATABASE_URL (Supabase Session pooler URI)}"
REGION=${REGION:-us-east1}
SERVICE=${SERVICE:-marketplace-api}
REPOSITORY=${REPOSITORY:-marketplace}
RUNTIME_SA="${SERVICE}@${GCP_PROJECT}.iam.gserviceaccount.com"

gcloud config set project "$GCP_PROJECT"
cd "$(dirname "$0")/.."
# shellcheck source=deploy/lib.sh
source deploy/lib.sh

echo "Enabling APIs..."
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  iam.googleapis.com

if ! gcloud artifacts repositories describe "$REPOSITORY" --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPOSITORY" --location "$REGION" \
    --repository-format docker --description "Marketplace API images"
fi
# Keep only recent images, inside Artifact Registry's free 0.5 GB.
gcloud artifacts repositories set-cleanup-policies "$REPOSITORY" --location "$REGION" \
  --policy deploy/artifact-cleanup.json --no-dry-run >/dev/null

if ! gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create "$SERVICE" --display-name "Marketplace API runtime"
fi

put_secret() {
  local name=$1 value=$2
  if gcloud secrets describe "$name" >/dev/null 2>&1; then
    printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=-
    # Secret Manager's free tier counts every non-destroyed version, so
    # destroy the ones this new version replaces.
    gcloud secrets versions list "$name" --filter="state!=DESTROYED" \
        --sort-by=~createTime --format='value(name)' | tail -n +2 |
      while read -r old; do
        gcloud secrets versions destroy "$old" --secret="$name" --quiet >/dev/null
      done
  else
    printf '%s' "$value" | gcloud secrets create "$name" --replication-policy=automatic --data-file=-
  fi
  retry gcloud secrets add-iam-policy-binding "$name" \
    --member="serviceAccount:${RUNTIME_SA}" --role=roles/secretmanager.secretAccessor >/dev/null
  echo "Secret $name stored."
}

put_secret database-url "$DATABASE_URL"
# Sentry is optional: without the secret, the deploy script leaves it disabled.
if [ -n "${SENTRY_DSN:-}" ]; then
  put_secret sentry-dsn "$SENTRY_DSN"
fi
# Payments are off (503) until these exist. The webhook secret belongs to the
# dashboard endpoint for the deployed API, not the one `stripe listen` prints.
if [ -n "${STRIPE_SECRET_KEY:-}" ]; then
  put_secret stripe-secret-key "$STRIPE_SECRET_KEY"
fi
if [ -n "${STRIPE_WEBHOOK_SECRET_PROD:-}" ]; then
  put_secret stripe-webhook-secret "$STRIPE_WEBHOOK_SECRET_PROD"
fi
# Scan photo uploads are off (503) until this exists; the rest of the bucket
# settings are in cloudrun.env.yaml.
if [ -n "${STORAGE_SECRET_ACCESS_KEY:-}" ]; then
  put_secret storage-secret-access-key "$STORAGE_SECRET_ACCESS_KEY"
fi
# Emails are recorded as skipped until this exists; EMAIL_FROM is in cloudrun.env.yaml.
if [ -n "${RESEND_API_KEY:-}" ]; then
  put_secret resend-api-key "$RESEND_API_KEY"
fi

echo "Done. Next: GCP_PROJECT=$GCP_PROJECT ./deploy/github-deploy-setup.sh (deploys from GitHub),"
echo "or deploy from this machine with Docker running: GCP_PROJECT=$GCP_PROJECT ./deploy/cloudrun-deploy.sh"
