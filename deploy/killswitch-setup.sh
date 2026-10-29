#!/usr/bin/env bash
# Budget alerts, free-tier usage checks and an automatic kill switch for the
# Cloud Run API.
#
# Creates (or updates):
#   * a monthly budget for this project, emailing billing admins at 50%, 90%, 100%
#   * a Pub/Sub topic the budget publishes its status to
#   * an hourly Cloud Scheduler job that asks for a free-tier usage check
#   * the api-killswitch function, which closes the API to public traffic once
#       - spend reaches STOP_AT_FRACTION of the budget (default 0.9, leaving
#         headroom for billing data that lags by hours), or
#       - this month's Cloud Run requests, vCPU-seconds, GiB-seconds, outbound
#         data or Artifact Registry storage reach FREE_TIER_FRACTION of the
#         free tier (default 0.8)
#     See deploy/killswitch/main.py.
#
# Usage (from the repo root, after the API has been deployed once):
#   GCP_PROJECT=my-project ./deploy/killswitch-setup.sh
#   GCP_PROJECT=my-project BUDGET_USD=5 FREE_TIER_FRACTION=0.5 ./deploy/killswitch-setup.sh
#
# You need Billing Account Administrator (or Costs Manager) on the project's
# billing account to create the budget.
set -euo pipefail

: "${GCP_PROJECT:?Set GCP_PROJECT to your Google Cloud project id}"
REGION=${REGION:-us-east1}
SERVICE=${SERVICE:-marketplace-api}
BUDGET_USD=${BUDGET_USD:-1}
STOP_AT_FRACTION=${STOP_AT_FRACTION:-0.9}
FREE_TIER_FRACTION=${FREE_TIER_FRACTION:-0.8}
TOPIC=budget-alerts
FUNCTION=api-killswitch
USAGE_JOB=free-tier-check
BUDGET_NAME="$SERVICE monthly budget"
KILLSWITCH_SA="killswitch@${GCP_PROJECT}.iam.gserviceaccount.com"

cd "$(dirname "$0")/.."
gcloud config set project "$GCP_PROJECT" >/dev/null

echo "Enabling APIs..."
gcloud services enable \
  cloudfunctions.googleapis.com \
  eventarc.googleapis.com \
  pubsub.googleapis.com \
  billingbudgets.googleapis.com \
  cloudbilling.googleapis.com \
  cloudscheduler.googleapis.com \
  monitoring.googleapis.com \
  artifactregistry.googleapis.com

PROJECT_NUMBER=$(gcloud projects describe "$GCP_PROJECT" --format='value(projectNumber)')
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

if ! gcloud pubsub topics describe "$TOPIC" >/dev/null 2>&1; then
  gcloud pubsub topics create "$TOPIC"
fi

if ! gcloud iam service-accounts describe "$KILLSWITCH_SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create killswitch --display-name "Budget kill switch"
fi

echo "Granting the kill switch permission to change the API service only..."
gcloud run services add-iam-policy-binding "$SERVICE" --region "$REGION" \
  --member "serviceAccount:${KILLSWITCH_SA}" --role roles/run.developer >/dev/null
# Updating a Cloud Run service requires acting as its runtime service account.
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member "serviceAccount:${KILLSWITCH_SA}" --role roles/iam.serviceAccountUser >/dev/null
gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
  --member "serviceAccount:${KILLSWITCH_SA}" --role roles/eventarc.eventReceiver >/dev/null
# Read-only access for the usage check: Cloud Run metrics and repository sizes.
for role in roles/monitoring.viewer roles/artifactregistry.reader; do
  gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
    --member "serviceAccount:${KILLSWITCH_SA}" --role "$role" >/dev/null
done

echo "Deploying the kill-switch function..."
gcloud functions deploy "$FUNCTION" \
  --gen2 \
  --region "$REGION" \
  --runtime python312 \
  --source deploy/killswitch \
  --entry-point stop_api \
  --trigger-topic "$TOPIC" \
  --service-account "$KILLSWITCH_SA" \
  --trigger-service-account "$KILLSWITCH_SA" \
  --set-env-vars "TARGET_SERVICE=projects/${GCP_PROJECT}/locations/${REGION}/services/${SERVICE},STOP_AT_FRACTION=${STOP_AT_FRACTION},FREE_TIER_FRACTION=${FREE_TIER_FRACTION}" \
  --memory 256Mi \
  --max-instances 1 \
  --no-allow-unauthenticated
gcloud functions add-invoker-policy-binding "$FUNCTION" --region "$REGION" \
  --member "serviceAccount:${KILLSWITCH_SA}" >/dev/null

# The function's own images are stored in gcf-artifacts; keep that inside the
# free 0.5 GB of Artifact Registry storage too.
if gcloud artifacts repositories describe gcf-artifacts --location "$REGION" >/dev/null 2>&1; then
  gcloud artifacts repositories set-cleanup-policies gcf-artifacts --location "$REGION" \
    --policy deploy/artifact-cleanup.json --no-dry-run >/dev/null
fi

echo "Scheduling the hourly free-tier usage check..."
USAGE_JOB_FLAGS=(
  --location "$REGION"
  --schedule "0 * * * *"
  --time-zone "Etc/UTC"
  --topic "$TOPIC"
  --message-body '{"check": "usage"}'
)
if gcloud scheduler jobs describe "$USAGE_JOB" --location "$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update pubsub "$USAGE_JOB" "${USAGE_JOB_FLAGS[@]}" >/dev/null
else
  gcloud scheduler jobs create pubsub "$USAGE_JOB" "${USAGE_JOB_FLAGS[@]}" >/dev/null
fi

echo "Creating the budget..."
BILLING_ACCOUNT=$(gcloud billing projects describe "$GCP_PROJECT" --format='value(billingAccountName)')
BILLING_ACCOUNT=${BILLING_ACCOUNT#billingAccounts/}
if [ -z "$BILLING_ACCOUNT" ]; then
  echo "Project $GCP_PROJECT has no billing account linked." >&2
  exit 1
fi
# The Budgets API needs a project to bill the API calls to (a "quota project").
EXISTING=$(gcloud billing budgets list --billing-project "$GCP_PROJECT" --billing-account "$BILLING_ACCOUNT" \
  --filter "displayName=\"$BUDGET_NAME\"" --format 'value(name)')
BUDGET_FLAGS=(
  --budget-amount "${BUDGET_USD}USD"
  --notifications-rule-pubsub-topic "projects/${GCP_PROJECT}/topics/${TOPIC}"
)
if [ -n "$EXISTING" ]; then
  gcloud billing budgets update "$EXISTING" --billing-project "$GCP_PROJECT" --billing-account "$BILLING_ACCOUNT" "${BUDGET_FLAGS[@]}"
else
  gcloud billing budgets create --billing-project "$GCP_PROJECT" --billing-account "$BILLING_ACCOUNT" \
    --display-name "$BUDGET_NAME" \
    --filter-projects "projects/${GCP_PROJECT}" \
    --calendar-period month \
    --threshold-rule percent=0.5 \
    --threshold-rule percent=0.9 \
    --threshold-rule percent=1.0 \
    "${BUDGET_FLAGS[@]}"
fi

echo "Done. $SERVICE stops serving public traffic when either:"
echo "  - spend reaches ${STOP_AT_FRACTION} of the \$${BUDGET_USD}/month budget, or"
echo "  - this month's Cloud Run usage reaches ${FREE_TIER_FRACTION} of the free tier (checked hourly)."
echo "Each hour's usage is logged: gcloud functions logs read $FUNCTION --region $REGION"
echo "Reopen with: GCP_PROJECT=$GCP_PROJECT ./deploy/cloudrun-reopen.sh"
