# marketplace-backend

FastAPI backend for an MTG card marketplace. Postgres and auth come from Supabase; the API is deployed on Google Cloud Run (a Fly.io config is kept in `fly.toml`).

## Configuration

Settings are read from environment variables (or `src/.env` locally):

| Variable | Purpose |
|---|---|
| `SUPABASE_PROJECT_REF` | Used to verify Supabase JWTs |
| `DATABASE_URL` | Supabase Postgres connection string (Session pooler URI). `postgres://` URLs are converted to asyncpg automatically |
| `SENTRY_DSN` | Optional; Sentry is off when unset |
| `SENTRY_TRACES_SAMPLE_RATE` | Defaults to `0.1` |
| `ENVIRONMENT` | Sentry environment name, defaults to `development` |
| `CORS_ORIGINS` | Comma-separated browser origins allowed to call the API. Defaults to the Expo web dev server (`http://localhost:8081`); production values are in `deploy/cloudrun.env.yaml` and `fly.toml` |
| `CLIENT_IP_SOURCE` | Where the per-IP rate limit gets the client address: `socket` (default, local), `fly`, or `x-forwarded-for` (Cloud Run) |
| `FORWARDED_FOR_HOPS` | With `x-forwarded-for`: which entry from the right is the client. `1` on Cloud Run, `2` behind a Google load balancer |
| `RATE_LIMIT_ENABLED` | `true` by default |
| `RATE_LIMIT_PER_IP_PER_MINUTE` | Per-IP ceiling across all endpoints, default `300`. Per-user write limits are in `src/utils/limits.py` |

## Access rules

Every authenticated request is checked against the database:
- the Supabase user must have a confirmed email (`403 email_unconfirmed`)
- banned accounts are rejected everywhere (`403 account_banned`)
- suspended accounts can read but not write (`403 account_suspended`)

Rate limits are kept in process memory, so they assume a single instance (Cloud Run `--max-instances 1`, or one Fly machine). Move them to Redis before running several instances.

## Running locally

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cd src && ../.venv/bin/uvicorn app:app --reload
```

API docs: http://localhost:8000/docs. All endpoints live under `/v1`.

## Card catalog

`cards` holds one row per paper printing, synced from Scryfall's `default_cards` bulk file by `src/scripts/scryfall_sync.py`. Scryfall's TCGplayer/Cardmarket prices go in `card_prices` and are shown only as an outside reference (`reference_prices`). Card images are hotlinked from Scryfall, not stored.

- **Nightly:** `.github/workflows/scryfall-sync.yml` runs the sync at 09:17 UTC on GitHub's runners. It needs a `DATABASE_URL` secret (and optionally a `SCRYFALL_USER_AGENT` variable) on the `production` environment. Start the first run by hand from the Actions tab.
- **Locally:** `cd src && ../.venv/bin/python -m scripts.scryfall_sync` (or `--file default-cards.json` for a downloaded copy). Re-running is safe: rows are matched on `scryfall_id`, and unchanged rows aren't rewritten.

Endpoints (no login needed):

| Endpoint | Returns |
|---|---|
| `GET /v1/cards/autocomplete?q=` | Up to 20 card names (one per card), prefix matches first, typo-tolerant |
| `GET /v1/cards/search` | Printings sorted by name; filters `q`, `set`, `rarity`, `type`, `colors` (color identity includes all, e.g. `UR`), `finish`, `lang` |
| `GET /v1/cards/oracle/{oracle_id}/printings` | Every printing of one card, newest first |
| `GET /v1/cards/{id}` | One printing, with reference prices |

## Migrations

Alembic is set up in `src/`. After changing `models/database.py`:

```bash
cd src && ../.venv/bin/alembic revision --autogenerate -m "describe change"
cd src && ../.venv/bin/alembic upgrade head
```

The production database predates Alembic. Before the first migration runs there, mark it as already at the baseline with `alembic stamp 0001` instead of running the baseline.

## Tests

Tests need a disposable Postgres database (**its contents are dropped**):

```bash
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/marketplace_test .venv/bin/pytest
```

Without `TEST_DATABASE_URL`, only the tests that don't need a database run. CI (`.github/workflows/ci.yml`) runs ruff, mypy and the full test suite against Postgres 15 on pull requests and branch pushes; on `main` it runs as the first step of the Deploy workflow.

## Deploying to Google Cloud Run

Pushes to `main` deploy automatically: `.github/workflows/deploy.yml` runs the test suite, then builds the Docker image, pushes it to Artifact Registry and deploys it with `deploy/cloudrun-deploy.sh`. GitHub signs in to Google Cloud with Workload Identity Federation, so no keys are stored in GitHub.

One-time setup (needs a Google Cloud project with billing enabled, `gcloud auth login`, and the project id):

```bash
set -a; source src/.env; set +a
GCP_PROJECT=your-project-id ./deploy/cloudrun-setup.sh
GCP_PROJECT=your-project-id ./deploy/github-deploy-setup.sh
```

1. `cloudrun-setup.sh` enables the APIs, creates the `marketplace` image repository (keeps the 2 newest images), creates the `marketplace-api` runtime service account, and stores `DATABASE_URL` (and `SENTRY_DSN` if set) in Secret Manager, readable only by that account.
2. `github-deploy-setup.sh` creates the `github-deployer` service account and lets only workflow runs on this repository's `main` branch use it. It then sets the `GCP_PROJECT_ID`, `GCP_WIF_PROVIDER` and `GCP_DEPLOY_SA` repository variables (with the GitHub CLI if installed; otherwise it prints them to add under Settings → Secrets and variables → Actions → Variables).
3. Push to `main` (or run the Deploy workflow by hand) for the first deploy, then set up the kill switch below.

Migrations aren't run by the workflow: run `alembic upgrade head` from `src/` before merging a change that needs one.

To deploy from your own machine instead (Docker must be running):

```bash
GCP_PROJECT=your-project-id ./deploy/cloudrun-deploy.sh
```

Defaults: region `us-east1` (a free-tier region, near Supabase in us-east), service `marketplace-api`, one instance at most, scale to zero when idle (free tier, with a cold start of a few seconds). Override with `REGION=…`, `SERVICE=…`, or `MIN_INSTANCES=1` to stay warm (billed). Non-secret settings are in `deploy/cloudrun.env.yaml`. To change a secret, re-run `cloudrun-setup.sh`; the next deploy picks it up.

### Kill switch: budget and free-tier limits

Google Cloud doesn't stop at the free tier on its own; budgets only send alerts. After the first deploy, set up the kill switch:

```bash
GCP_PROJECT=your-project-id ./deploy/killswitch-setup.sh
```

The `api-killswitch` function (`deploy/killswitch/`) closes the API when either check trips. Closing sets the service's ingress to internal-only: the public URL stops answering and stops costing money, but nothing is deleted.

| Check | Runs | Trips at | Source |
|---|---|---|---|
| Budget ($1/month by default) | several times a day | 90% of the budget (`STOP_AT_FRACTION`) | Cloud Billing, which lags hours to a day |
| Free-tier usage, month to date | hourly (Cloud Scheduler) | 80% of any allowance (`FREE_TIER_FRACTION`) | Cloud Monitoring, minutes behind |

The usage check measures, for this service only:

| Measure | Free allowance | How it's measured |
|---|---|---|
| Requests | 2,000,000 | `request_count` |
| vCPU-seconds | 180,000 (about 50 busy hours at 1 vCPU) | `billable_instance_time` × the service's CPU limit |
| GiB-seconds | 360,000 | `billable_instance_time` × the service's memory limit |
| Outbound data | 1 GB | `sent_bytes_count`, all traffic, including queries to Supabase (counts more than Google bills, on purpose) |
| Artifact Registry storage | 0.5 GB | size of every repository in the region (API and kill-switch images) |

Allowances default to Google's published limits and can be overridden with `FREE_REQUESTS`, `FREE_VCPU_SECONDS`, `FREE_GIB_SECONDS`, `FREE_EGRESS_BYTES` and `FREE_ARTIFACT_BYTES` on the function. Free-tier limits are per billing account, so other projects on the same account use the same allowance without being counted here; lower `FREE_TIER_FRACTION` if you have any. Each hourly check logs a usage summary:

```bash
gcloud functions logs read api-killswitch --region us-east1
```

To turn the API back on:

```bash
GCP_PROJECT=your-project-id ./deploy/cloudrun-reopen.sh
```

A tripped check keeps tripping until its cause goes away, so reopening in the same month closes the API again within the hour (usage) or at the next budget update. Wait for the new month (Pacific time), raise the limit, or, for Artifact Registry storage, delete old images. Deploying doesn't reopen a closed API.

Two things keep normal costs at zero: the setup scripts set Artifact Registry cleanup policies (keep the 2 newest images), and the setup script destroys replaced secret versions (Secret Manager's free tier counts every version that isn't destroyed).
