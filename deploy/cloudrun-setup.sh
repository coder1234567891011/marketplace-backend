#!/usr/bin/env bash
# One-time Google Cloud setup for the Cloud Run deployment.
#
# Usage (from the repo root, with gcloud installed and logged in):
#   set -a; source src/.env; set +a        # provides DATABASE_URL and SENTRY_DSN
#   GCP_PROJECT=my-project ./deploy/cloudrun-setup.sh
#
# Safe to re-run: existing secrets get a new version instead of failing.
set -euo pipefail

: "${GCP_PROJECT:?Set GCP_PROJECT to your Google Cloud project id}"
: "${DATABASE_URL:?Set DATABASE_URL (Supabase Session pooler URI)}"

gcloud config set project "$GCP_PROJECT"

echo "Enabling APIs..."
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com

# Cloud Run (and source builds) run as the default compute service account.
PROJECT_NUMBER=$(gcloud projects describe "$GCP_PROJECT" --format='value(projectNumber)')
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

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
  gcloud secrets add-iam-policy-binding "$name" \
    --member="serviceAccount:${RUNTIME_SA}" --role=roles/secretmanager.secretAccessor >/dev/null
  echo "Secret $name stored."
}

put_secret database-url "$DATABASE_URL"
# Sentry is optional: without the secret, the deploy script leaves it disabled.
if [ -n "${SENTRY_DSN:-}" ]; then
  put_secret sentry-dsn "$SENTRY_DSN"
fi

echo "Allowing source deploys to build images..."
gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
  --member="serviceAccount:${RUNTIME_SA}" --role=roles/run.builder >/dev/null

echo "Done. Deploy with: GCP_PROJECT=$GCP_PROJECT ./deploy/cloudrun-deploy.sh"
