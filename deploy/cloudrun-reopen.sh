#!/usr/bin/env bash
# Re-open the API to public traffic after the budget kill switch closed it.
#
# Usage: GCP_PROJECT=my-project ./deploy/cloudrun-reopen.sh
#
# If this month's spend is still over the budget, the next budget update
# (within hours) will close it again: raise BUDGET_USD and re-run
# killswitch-setup.sh, or wait for the next month.
set -euo pipefail

: "${GCP_PROJECT:?Set GCP_PROJECT to your Google Cloud project id}"
REGION=${REGION:-us-east1}
SERVICE=${SERVICE:-marketplace-api}

gcloud run services update "$SERVICE" --project "$GCP_PROJECT" --region "$REGION" --ingress all
echo "$SERVICE is serving public traffic again."
