#!/usr/bin/env bash
# Build the API image, push it to Artifact Registry and deploy it to Cloud Run.
# GitHub Actions runs this on every push to main (.github/workflows/deploy.yml);
# it also works from a laptop with Docker running.
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
REPOSITORY=${REPOSITORY:-marketplace}
RUNTIME_SA="${SERVICE}@${GCP_PROJECT}.iam.gserviceaccount.com"
# 0 = free tier with cold starts after idle; 1 = always warm (billed).
MIN_INSTANCES=${MIN_INSTANCES:-0}

cd "$(dirname "$0")/.."

# Tag images with the commit; local builds from uncommitted changes get a suffix.
TAG=${IMAGE_TAG:-$(git rev-parse --short HEAD)}
if [ -z "${IMAGE_TAG:-}" ] && ! git diff --quiet HEAD; then
  TAG="${TAG}-dirty-$(date +%s)"
fi
IMAGE="${REGION}-docker.pkg.dev/${GCP_PROJECT}/${REPOSITORY}/${SERVICE}:${TAG}"

echo "Building $IMAGE..."
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet >/dev/null
docker build --platform linux/amd64 --tag "$IMAGE" .
docker push "$IMAGE"

SECRETS="DATABASE_URL=database-url:latest"
# Optional secrets: each is passed only once it exists in Secret Manager.
for pair in SENTRY_DSN=sentry-dsn STRIPE_SECRET_KEY=stripe-secret-key STRIPE_WEBHOOK_SECRET=stripe-webhook-secret \
    STORAGE_SECRET_ACCESS_KEY=storage-secret-access-key; do
  if gcloud secrets describe "${pair#*=}" --project "$GCP_PROJECT" >/dev/null 2>&1; then
    SECRETS="$SECRETS,${pair}:latest"
  fi
done

# --max-instances 1: rate-limit counters are kept in memory, so they're only
# accurate with a single instance.
gcloud run deploy "$SERVICE" \
  --project "$GCP_PROJECT" \
  --region "$REGION" \
  --image "$IMAGE" \
  --service-account "$RUNTIME_SA" \
  --allow-unauthenticated \
  --min-instances "$MIN_INSTANCES" \
  --max-instances 1 \
  --cpu 1 \
  --memory 512Mi \
  --concurrency 80 \
  --timeout 60 \
  --env-vars-file deploy/cloudrun.env.yaml \
  --set-secrets "$SECRETS" \
  --quiet

URL=$(gcloud run services describe "$SERVICE" --project "$GCP_PROJECT" --region "$REGION" --format='value(status.url)')
echo "Deployed: $URL"
# A closed API (see deploy/killswitch) still deploys, so don't fail on this.
if ! curl -fsS --max-time 30 "$URL/health"; then
  echo "Warning: $URL/health didn't answer. If the kill switch closed the API, see deploy/cloudrun-reopen.sh." >&2
fi
echo
