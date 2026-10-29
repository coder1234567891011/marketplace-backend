#!/usr/bin/env bash
# Let GitHub Actions deploy to Cloud Run without any stored keys.
#
# Sets up Workload Identity Federation: GitHub's short-lived OIDC token for a
# workflow run on this repo's main branch is exchanged for access as a
# dedicated "github-deployer" service account, which can only push images and
# deploy the API. Then it sets the repository variables the deploy workflow
# reads (none of them are secrets).
#
# Usage (from the repo root, after cloudrun-setup.sh):
#   GCP_PROJECT=my-project ./deploy/github-deploy-setup.sh
#
# GITHUB_REPO defaults to this checkout's origin remote (owner/name).
set -euo pipefail

: "${GCP_PROJECT:?Set GCP_PROJECT to your Google Cloud project id}"
REGION=${REGION:-us-east1}
SERVICE=${SERVICE:-marketplace-api}
REPOSITORY=${REPOSITORY:-marketplace}
DEPLOY_BRANCH=${DEPLOY_BRANCH:-main}
POOL=github
PROVIDER=github
DEPLOYER_SA="github-deployer@${GCP_PROJECT}.iam.gserviceaccount.com"
RUNTIME_SA="${SERVICE}@${GCP_PROJECT}.iam.gserviceaccount.com"

cd "$(dirname "$0")/.."
# shellcheck source=deploy/lib.sh
source deploy/lib.sh
if [ -z "${GITHUB_REPO:-}" ]; then
  GITHUB_REPO=$(git remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
fi
echo "GitHub repository: $GITHUB_REPO (deploys from $DEPLOY_BRANCH)"

gcloud config set project "$GCP_PROJECT" >/dev/null
PROJECT_NUMBER=$(gcloud projects describe "$GCP_PROJECT" --format='value(projectNumber)')

gcloud services enable iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com

if ! gcloud iam workload-identity-pools describe "$POOL" --location global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "$POOL" --location global --display-name "GitHub Actions"
fi

# Only tokens from this repository's deploy branch are accepted.
CONDITION="assertion.repository == '${GITHUB_REPO}' && assertion.ref == 'refs/heads/${DEPLOY_BRANCH}'"
PROVIDER_FLAGS=(
  --location global
  --workload-identity-pool "$POOL"
  --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref"
  --attribute-condition "$CONDITION"
)
if gcloud iam workload-identity-pools providers describe "$PROVIDER" \
    --location global --workload-identity-pool "$POOL" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers update-oidc "$PROVIDER" "${PROVIDER_FLAGS[@]}" >/dev/null
else
  gcloud iam workload-identity-pools providers create-oidc "$PROVIDER" "${PROVIDER_FLAGS[@]}" \
    --issuer-uri "https://token.actions.githubusercontent.com" --display-name "GitHub"
fi
WIF_PROVIDER="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"

if ! gcloud iam service-accounts describe "$DEPLOYER_SA" >/dev/null 2>&1; then
  gcloud iam service-accounts create github-deployer --display-name "GitHub Actions deployer"
fi

echo "Granting the deployer only what a deploy needs..."
# Workflow runs from the repository may act as the deployer.
retry gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER_SA" --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${GITHUB_REPO}" \
  >/dev/null
# Push images to the API's repository only.
retry gcloud artifacts repositories add-iam-policy-binding "$REPOSITORY" --location "$REGION" \
  --member "serviceAccount:${DEPLOYER_SA}" --role roles/artifactregistry.writer >/dev/null
# Create and update Cloud Run services (project-wide so the first deploy can create it).
retry gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
  --member "serviceAccount:${DEPLOYER_SA}" --role roles/run.developer >/dev/null
# Deploying a service that runs as the API's runtime account.
retry gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member "serviceAccount:${DEPLOYER_SA}" --role roles/iam.serviceAccountUser >/dev/null
# See which secrets exist (metadata only, not their values).
retry gcloud projects add-iam-policy-binding "$GCP_PROJECT" \
  --member "serviceAccount:${DEPLOYER_SA}" --role roles/secretmanager.viewer >/dev/null

echo
echo "Repository variables for .github/workflows/deploy.yml:"
echo "  GCP_PROJECT_ID   = $GCP_PROJECT"
echo "  GCP_WIF_PROVIDER = $WIF_PROVIDER"
echo "  GCP_DEPLOY_SA    = $DEPLOYER_SA"
if command -v gh >/dev/null 2>&1; then
  gh variable set GCP_PROJECT_ID --repo "$GITHUB_REPO" --body "$GCP_PROJECT"
  gh variable set GCP_WIF_PROVIDER --repo "$GITHUB_REPO" --body "$WIF_PROVIDER"
  gh variable set GCP_DEPLOY_SA --repo "$GITHUB_REPO" --body "$DEPLOYER_SA"
  echo "Set with the GitHub CLI."
else
  echo "Add them under GitHub → $GITHUB_REPO → Settings → Secrets and variables → Actions → Variables."
fi
