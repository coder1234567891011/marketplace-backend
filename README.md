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
| `STRIPE_SECRET_KEY` | Stripe secret key (`sk_test_…` until launch). Payment endpoints return 503 without it |
| `STRIPE_WEBHOOK_SECRET` | Signing secret of the webhook endpoint (`whsec_…`) |
| `FRONTEND_URL` | The web app; Stripe sends buyers and sellers back here. Defaults to `http://localhost:8081` |
| `PLATFORM_FEE_PERCENT`, `PLATFORM_FEE_FIXED_CENTS`, `PLATFORM_FEE_MIN_CENTS`, `CHECKOUT_HOLD_MINUTES`, `PAYOUT_ON` | Marketplace terms, see "Cart, checkout and payments" |
| `STORAGE_ENDPOINT`, `STORAGE_BUCKET`, `STORAGE_REGION`, `STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY` | S3-compatible bucket for scan photos, see "Card scanning". Photo uploads return 503 without them |
| `MARKET_PRICE_DAYS`, `MARKET_PRICE_SALES`, `MARKET_PRICE_MIN_SALES`, `CONDITION_PRICE_FACTORS` | How market prices are worked out, see "Market prices" |
| `ISSUE_WINDOW_DAYS`, `ISSUE_SELLER_DAYS`, `AUTO_DELIVER_DAYS` | Buyer protection, see "Order issues" (placeholders: 30, 3, 21) |
| `RESEND_API_KEY`, `EMAIL_FROM`, `EMAIL_MAX_ATTEMPTS` | Transactional email, see "Emails". Emails are recorded as `skipped` without them |

## Access rules

Every authenticated request is checked against the database:
- the Supabase user must have a confirmed email (`403 email_unconfirmed`)
- banned accounts are rejected everywhere (`403 account_banned`)
- suspended accounts can read but not write (`403 account_suspended`)
- `/v1/admin` endpoints need a moderator (`users.is_admin`, `403 not_moderator`; see "Moderation")

Rate limits are kept in process memory, so they assume a single instance (Cloud Run `--max-instances 1`, or one Fly machine). Move them to Redis before running several instances.

## Running locally

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cd src && ../.venv/bin/uvicorn app:app --reload
```

API docs: http://localhost:8000/docs. All endpoints live under `/v1`.

### API spec and Postman collection

- **`api-spec.yaml`:** the OpenAPI 3.1 description, the same as `/openapi.json`. It shows which endpoints need a bearer token (`security`) and the error format (`Error`).
- **`postman_collection.json`:** one folder per area, with example bodies.
  - Import it into Postman, then set `supabaseAnonKey`, `email` and `password` in the collection variables.
  - Run **auth → Sign in (Supabase)** to store a token in `{{token}}`, which every request sends.
  - `baseUrl` defaults to `http://localhost:8000`; point it at Cloud Run for the deployed API.

Both are generated from the app. Regenerate them after changing endpoints (a test fails while they're out of date):

```bash
cd src && ../.venv/bin/python -m scripts.export_api
```

## Card catalog

`cards` holds one row per paper printing, synced from Scryfall's `default_cards` bulk file by `src/scripts/scryfall_sync.py`. Scryfall's TCGplayer/Cardmarket prices go in `card_prices` and are shown only as an outside reference (`reference_prices`). Card images are hotlinked from Scryfall, not stored.

- **Nightly:** `.github/workflows/scryfall-sync.yml` runs the sync at 09:17 UTC on GitHub's runners. It needs a `DATABASE_URL` secret (and optionally a `SCRYFALL_USER_AGENT` variable) on the `production` environment. Start the first run by hand from the Actions tab.
- **Locally:** `cd src && ../.venv/bin/python -m scripts.scryfall_sync` (or `--file default-cards.jsonl.gz` for a downloaded copy). Re-running is safe: rows are matched on `scryfall_id`, and unchanged rows aren't rewritten.

Endpoints (no login needed):

| Endpoint | Returns |
|---|---|
| `GET /v1/cards/autocomplete?q=` | Up to 20 card names (one per card), prefix matches first, typo-tolerant |
| `GET /v1/cards/search` | Printings sorted by name; filters `q`, `set`, `rarity`, `type`, `colors` (color identity includes all, e.g. `UR`), `finish`, `lang` |
| `GET /v1/cards/oracle/{oracle_id}/printings` | Every printing of one card, newest first |
| `GET /v1/cards/{id}` | One printing, with marketplace prices (`market_prices`) and Scryfall's reference prices |
| `GET /v1/cards/{id}/sales` | Recent sales, newest first; filters `finish`, `language`, `condition`, `limit` |
| `GET /v1/cards/{id}/price-history` | NM market price changes for `finish` and `language` over `days` (default 90) |
| `GET /v1/cards/{id}/suggested-price` | A starting price for a listing in `condition`, `finish` and `language` |

## Collections, listings and shipping

- **Finish and language:** collection items and listings have `finish` (`nonfoil`, `foil`, `etched`, checked against the printing's finishes) and `language` (Scryfall codes such as `en`, `ja`).
- **Import:** `POST /v1/collections/{id}/import` takes `{"format": "decklist" | "csv", "text": …}`. Decklists accept Arena/MTGO lines (`4 Lightning Bolt (M10) 146 *F*`); CSVs need a header row (Moxfield, ManaBox, Deckbox exports work). Add `?dry_run=true` to preview. Unmatched lines are returned, not fatal; lines without a set get the newest released, regular printing and a `note` saying so. Cards already in the collection (same printing, finish, condition, language) get their quantity raised.
- **Reserved copies:** a listing made from a collection item holds `quantity_available` plus `quantity_reserved` (copies in buyers' open checkouts) of its copies while active or pending sale (`quantity_listed` on the item). You can't list more than are free, lower the item below what's listed, or delete it while listed.
- **Listing status:** `active → pending_sale → sold`, `pending_sale → active` (checkout released), `active → removed`. Sellers only remove (not while copies are in a checkout); checkout makes the other changes.
- **Browsing (no login):** `GET /v1/listings/card/{card_id}` (cheapest first; filters `condition`, `finish`, `language`), `GET /v1/listings/card/{card_id}/summary` (per condition and finish: count, quantity, lowest price), `GET /v1/listings/user/{user_id}` (storefront; `q`, `sort=newest|price_asc|price_desc`). Listings from banned or suspended sellers, and from sellers who haven't finished payout setup, are hidden.
- **Shipping:** sellers keep up to 10 flat rates at `/v1/shipping-rates` (public list at `/v1/shipping-rates/user/{user_id}`).

## Cart, checkout and payments

Payments use Stripe: Checkout (Stripe's hosted payment page) and Connect Express accounts for sellers, with "separate charges and transfers": the buyer pays the platform once per checkout, and each seller's share is transferred to them. Every Stripe call goes through `services/payments/stripe_gateway.py`.

- **Sellers:** `POST /v1/sellers/me/onboarding` returns a Stripe link for payout setup; `GET /v1/sellers/me` says whether they `can_sell`; `POST /v1/sellers/me/dashboard` opens their Stripe dashboard. Listings can only be bought from sellers who can be paid.
- **Cart:** `PUT /v1/cart/items/{listing_id}` with `{"quantity": n}`, `DELETE` to remove, `GET /v1/cart` grouped by seller, with any lines that can't be bought (`problem`) and each seller's shipping rates. The cart holds nothing.
- **Checkout:** `POST /v1/checkout` with `{"shipping_address_id": …, "shipping_rates": {seller_id: rate_id}}` (sellers left out get their cheapest rate; the address needs a `recipient_name`). It creates one order per seller, holds the copies (`quantity_available → quantity_reserved`) and returns `url`, Stripe's payment page. The hold lasts `CHECKOUT_HOLD_MINUTES`; a buyer has one open checkout at a time and can cancel it (`POST /v1/checkout/{id}/cancel`). After paying, Stripe sends the buyer to `FRONTEND_URL/checkout/{id}?result=success`; poll `GET /v1/checkout/{id}` until `status` is `paid`.
- **Webhook:** `POST /v1/stripe/webhook`. Payment marks orders `paid`, sells the held copies (removing them from the seller's collection) and empties those cart lines; expiry puts the copies back on sale and cancels the orders. Events are recorded in `stripe_events`, so redeliveries are ignored.
- **Orders:** sellers `POST /v1/orders/{id}/ship` (optional `tracking_number`) or `/refund` (cancel an unshipped order with a full refund); buyers `POST /v1/orders/{id}/received`, which allows ratings. Shipped orders count as delivered after `AUTO_DELIVER_DAYS` if the buyer never says. Sellers only see orders once paid. Problems after payment go through order issues (below). A dispute webhook (the buyer's bank) marks the checkout's orders `disputed`, and the API won't refund those.
- **Fees and payouts:** each order records its fee terms, the `platform_fee_cents` and the `seller_payout_cents` (total minus fee). The seller is paid when they ship (`PAYOUT_ON=shipped`), when the buyer pays (`paid`), or once the order is delivered with no issue open (`delivered`). Refunds can be partial (`refunded_cents` on the order); each comes out of the seller's payout first (reversed from their Stripe account if they've been paid, otherwise taken off the payout), and only the part beyond the payout, i.e. the platform's fee on a full refund, is the platform's cost. Terms are settings (`PLATFORM_FEE_PERCENT`, `PLATFORM_FEE_FIXED_CENTS`, `PLATFORM_FEE_MIN_CENTS`, `CHECKOUT_HOLD_MINUTES`, `PAYOUT_ON` in `deploy/cloudrun.env.yaml`); the current values are placeholders.

### Testing payments locally

Put test keys in `src/.env`: `STRIPE_SECRET_KEY=sk_test_…`, plus the `whsec_…` secret that `stripe listen` prints as `STRIPE_WEBHOOK_SECRET`. Then, with the API running on port 8000:

```bash
stripe listen --forward-to localhost:8000/v1/stripe/webhook --forward-connect-to localhost:8000/v1/stripe/webhook
```

Pay on the checkout page with card `4242 4242 4242 4242`, any future date and any CVC. Test-mode sellers can finish onboarding with Stripe's test values (for example SSN `000-00-0000`). The automated tests use an in-memory fake (`tests/fake_stripe.py`) and never call Stripe.

### Deployed webhook

In the Stripe dashboard (test mode), go to Developers → Webhooks → Add endpoint, at `https://YOUR-SERVICE-URL/v1/stripe/webhook`. Choose "Listen to events on Connected accounts" as well as your account, and select `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.expired`, `charge.dispute.created` and `account.updated`. Store its signing secret and the secret key in Secret Manager, then redeploy:

```bash
set -a; source src/.env; set +a
STRIPE_WEBHOOK_SECRET_PROD=whsec_… GCP_PROJECT=your-project-id ./deploy/cloudrun-setup.sh
```

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

The workflow runs `alembic upgrade head` before building the image, using the `DATABASE_URL` secret on the `production` environment (the same one the Scryfall sync uses). A failed migration stops the deploy, and the old revision keeps serving. Because migrations run while the old revision is still live, keep them backward compatible: add columns and tables in one release and drop old ones in a later one.

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

## Wants lists and the cart optimizer

Lists of cards a user is looking for (up to 50 lists of 500 cards), under `/v1/wants`:

- **Items:** `POST /v1/wants/{id}/items` with a `card_id` and `any_printing` (default true; false wants exactly that printing), plus optional `quantity`, `min_condition` (the worst acceptable), `finish`, `language` and `max_price_cents`. Or paste a decklist: `POST /v1/wants/{id}/import` with `{"text": …}`. Lines with a set and number (`1 Lightning Helix (RAV) 213`) want that printing; the rest want any printing. Cards already on the list get their quantity raised.
- **Status:** `GET /v1/wants/{id}/items` shows each item with `owned` (copies in the caller's collections, any condition), `available` (matching copies for sale, not the caller's own) and `lowest_price_cents`.
- **Price alerts:** `GET /v1/wants/{id}/alerts` lists listings at or under an item's `max_price_cents`. Nothing is pushed yet; that comes with emails or notifications.
- **Optimizer:** `POST /v1/wants/{id}/optimize` (`skip_owned`, default true; `add_to_cart`) returns the cheapest way found to buy the list. It counts card prices plus each seller's cheapest shipping rate once per seller (what checkout uses by default), ignores sellers without shipping rates, and reports `missing` copies nobody sells. With `add_to_cart` the plan's listings go into the cart, replacing those listings' quantities already there.

The optimizer (`services/wants/optimizer.py`) is greedy: it fills each card from the cheapest offer, counting a seller's shipping when they're new to the plan, then moves cards off sellers whenever that lowers the total. It also tries building the plan around each of the 20 sellers who sell the most of the list. It isn't guaranteed to find the cheapest plan; an integer program (OR-Tools) would, if the savings ever justify it. A 100-card list across 200 sellers takes about a second.

## Card scanning

The app reads the text on a card with OCR on the device (for example Tesseract.js) and the API matches it against the catalog, so scanning costs nothing per card and doesn't load the server. Photos are optional and go straight from the app to a storage bucket.

1. `POST /v1/scans`, optionally with `photo: {content_type, size_bytes}` (JPEG, PNG, WebP or HEIC, up to `SCAN_PHOTO_MAX_BYTES`, 10 MB) and the text already read. The response's `upload` holds a URL to `PUT` the file to, with the given headers, within 10 minutes.
2. `POST /v1/scans/{id}/recognize` with `collector_text` (the bottom-left line, e.g. `0150 R` / `M21 • EN`) and/or `name_text`. You can also send them in step 1. It can be repeated, for example after a better photo.
3. The scan comes back `matched` with up to 5 `candidates` (best first, with a `confidence` from 0 to 1), or `unmatched`. When the top candidate is 0.9 or more the app can skip asking.
4. `POST /v1/scans/{id}/confirm` with `collection_id` (plus optional `card_id`, `condition`, `finish`, `language`, `quantity`) adds the card to the collection. It defaults to the top candidate and to the language and foil read from the collector line. The same card again raises the existing item's quantity. Items record `recognition_status` (`confirmed` from a candidate, `manual` when the user picked another card) and `recognition_confidence`.

Matching, in `services/scans/matching.py`:
- **Collector line:** set code plus collector number identifies the exact printing (cards since 2014). The language code picks between languages, and a ★ marks a foil.
- **Name:** typo-tolerant with pg_trgm. Double-faced cards match on their front face, and a set code read from the collector line chooses the printing. The catalog is mostly English, so non-English names rarely match by name alone.
- **Disagreement:** if the number points at a different card than the name, the name wins.

`GET /v1/scans` lists the caller's scans (`status` filter) and `GET /v1/scans/{id}` includes a temporary `photo_url`.

### Photo bucket (Cloudflare R2)

R2 is free up to 10 GB with no download fees; any S3-compatible store works (Supabase Storage, AWS S3). In the Cloudflare dashboard → R2:

1. Create a bucket, e.g. `mtg-scans`. `STORAGE_ENDPOINT` is `https://ACCOUNT_ID.r2.cloudflarestorage.com`, `STORAGE_REGION` is `auto`.
2. Settings → CORS policy, so browsers can upload and view (add `http://localhost:8081` for local work):
   ```json
   [{"AllowedOrigins": ["https://YOUR-FRONTEND-DOMAIN.example"], "AllowedMethods": ["PUT", "GET"], "AllowedHeaders": ["Content-Type"], "MaxAgeSeconds": 3600}]
   ```
3. Settings → Object lifecycle rules: delete objects with prefix `scans/` after 30 days. Deleting a scan doesn't delete its photo, so this keeps the bucket small.
4. Manage R2 API tokens → create a token with Object Read & Write on that bucket only. Put its access key id in `STORAGE_ACCESS_KEY_ID` (`deploy/cloudrun.env.yaml`), and store the secret:
   ```bash
   STORAGE_SECRET_ACCESS_KEY=… GCP_PROJECT=your-project-id ./deploy/cloudrun-setup.sh
   ```

## Market prices

Prices come from sales on the marketplace. Scryfall's TCGplayer and Cardmarket prices are only shown as `reference_prices`.

- **Sales:** order items of paid, shipped or delivered orders, keyed by card, finish, condition and language. Refunded, cancelled and disputed orders don't count. `GET /v1/cards/{id}/sales` shows them publicly (price, condition, date, never who bought or sold).
- **Market price:** one per card, finish and language, in NM condition. Each sale is scaled to NM with `CONDITION_PRICE_FACTORS` (placeholder: LP 0.9, MP 0.75, HP 0.6, DMG 0.4), then the price is the median of the last `MARKET_PRICE_SALES` (10) sales within `MARKET_PRICE_DAYS` (90), once there are at least `MARKET_PRICE_MIN_SALES` (3). Until then it is the cheapest NM listing for sale, marked `source: "listings"`.
- **Nightly job:** `src/scripts/market_prices.py` rebuilds `market_prices`, as the second step of the Scryfall sync workflow (it runs even if the sync fails). A sale shows up in the market price the next night. Run it by hand with `cd src && ../.venv/bin/python -m scripts.market_prices`.
- **History:** `price_snapshots` only gets a row on days a price changed, which keeps it small; the price on any day is the latest row on or before it. `price-history` starts with the price in force when the period began.
- **Suggested price:** the market price scaled to the listing's condition. With no market price yet, Scryfall's TCGplayer price for that finish, scaled the same way and marked `source: "reference"`, but only in the printing's own language.

## Order issues

Buyer protection after payment (`services/issues`).

- **Opening:** the buyer `POST /v1/orders/{id}/issues` with a `reason` (`not_received`, `not_as_described`, `other`) and a first `message`, on a paid, shipped or delivered order, until `ISSUE_WINDOW_DAYS` after delivery. One unresolved issue per order. The seller is emailed.
- **Sorting it out:** both sides `POST /v1/issues/{id}/messages`. The seller `POST /v1/issues/{id}/refund`, with `amount_cents` for a partial refund (default: everything left), which resolves it. The buyer can `withdraw`. Marking the order received closes a `not_received` issue, and cancelling an unshipped order settles any issue.
- **Escalating:** the seller any time, or the buyer once `escalate_after` has passed (`ISSUE_SELLER_DAYS` after opening), `POST /v1/issues/{id}/escalate`. A moderator then decides (below).
- **Effect on the order:** while an issue is unresolved the order isn't auto-delivered, and with `PAYOUT_ON=delivered` the seller isn't paid.
- **Listing:** `GET /v1/issues?role=buyer|seller&status=`, `GET /v1/orders/{id}/issues`, `GET /v1/issues/{id}` (with messages; `from_moderator` marks a moderator's).
- **Nightly job:** `src/scripts/order_maintenance.py` auto-delivers and, with `PAYOUT_ON=delivered`, pays sellers who are due (retrying failed payouts; failures fail the workflow run). It runs in the Scryfall sync workflow, so set the `production` environment's `STRIPE_SECRET_KEY` secret and `PAYOUT_ON` variable (matching `deploy/cloudrun.env.yaml`) there.

## Moderation

Moderators are users with `users.is_admin` set. There's no endpoint for it; in the Supabase SQL editor:

```sql
update users set is_admin = true where username = 'your-username';
```

- **Reports:** any user `POST /v1/reports` with `target_type` (`listing`, `user`, `rating`), `target_id`, `reason` and optional `details`; `GET /v1/reports` shows their own and whether they were acted on.
- **Queue:** `GET /v1/admin/reports` (open by default) and `GET /v1/admin/issues` (escalated by default).
- **Actions:** `PUT /v1/admin/users/{id}/status` (`active`, `suspended`: can sign in and read but not change anything, `banned`: nothing; their listings stop being for sale either way, and they're emailed), `POST /v1/admin/listings/{id}/remove`, `POST /v1/admin/ratings/{id}/remove` (the average is recalculated), `POST /v1/admin/reports/{id}/resolve` (`actioned` or `dismissed`). Acting on a target closes its open reports.
- **Issues:** `GET /v1/admin/issues/{id}`, `POST /v1/admin/issues/{id}/messages`, and `POST /v1/admin/issues/{id}/resolve` with `refund_cents` (0 decides for the seller) and a `note` that's posted to the issue and emailed with the outcome.
- **Log:** every action is in `moderation_actions`; `GET /v1/admin/actions?target_id=` shows them.

## Emails

Emails are written to the `emails` table in the same transaction as what they're about, then sent through [Resend](https://resend.com) right after the request commits; `src/scripts/send_emails.py` (nightly) retries what didn't go, up to `EMAIL_MAX_ATTEMPTS`. Addresses come from Supabase's `auth.users` when sending. Templates are plain text, in `src/services/email/templates.py`: order placed, sold, shipped and refunded; issue opened, message, escalated and resolved; account status changes.

To turn them on:

1. In Resend, add and verify a sending domain (DNS records), then create an API key with sending access.
2. Store the key: `RESEND_API_KEY=re_… GCP_PROJECT=your-project-id ./deploy/cloudrun-setup.sh`, and set `EMAIL_FROM` (e.g. `MTG Marketplace <orders@yourdomain>`) in `deploy/cloudrun.env.yaml`. Redeploy.
3. For the nightly retry, add the `RESEND_API_KEY` secret and the `EMAIL_FROM` and `FRONTEND_URL` variables to the `production` environment in GitHub.

Until then emails are marked `skipped`, so turning them on later doesn't send a backlog. Cloud Run slows the CPU between requests, so a background send can wait for the next request; the nightly run catches anything left.

## Payment testing checklist

The automated tests only use a fake Stripe. Run these by hand in Stripe test mode before relying on payments, first locally (see "Testing payments locally"), then once against the deployed API.

Locally:

- [ ] **Seller setup:** `POST /v1/sellers/me/onboarding`, finish Stripe's forms with test values, then `GET /v1/sellers/me` shows `can_sell: true`. This also confirms Stripe accepts US Express accounts that only request the `transfers` capability.
- [ ] **Account updates:** the `stripe listen` output shows `account.updated` arriving with a 200.
- [ ] **Multi-seller checkout:** cart with listings from two sellers, `POST /v1/checkout`, the Stripe page shows one line per seller with shipping included.
- [ ] **Paying:** card `4242 4242 4242 4242`. `GET /v1/checkout/{id}` turns `paid`, both orders are `paid`, the copies leave the sellers' collections and the cart is empty.
- [ ] **Declined card:** `4000 0000 0000 0002`. Nothing changes and the checkout stays `open`.
- [ ] **Shipping pays the seller:** `POST /v1/orders/{id}/ship`. A transfer of `seller_payout_cents` appears under Connect → the seller's account, and the order gets `paid_out_at`.
- [ ] **Refund:** `POST /v1/orders/{id}/refund` on an unshipped order. The payment shows a partial refund of that order's total. With `PAYOUT_ON=paid`, the transfer is reversed first.
- [ ] **Partial refund after shipping:** ship an order, open an issue as the buyer, then `POST /v1/issues/{id}/refund` with `amount_cents` as the seller. The payment shows that refund, and the seller's transfer shows a reversal of the same amount (Connect → the seller's account → Transfers).
- [ ] **Reversal with an empty balance:** repeat once the seller's test balance has been paid out to their bank. Note what Stripe does with the reversal (it may fail, returning 502); if it does, decide how the platform recovers it before launch.
- [ ] **Cancel:** `POST /v1/checkout/{id}/cancel`. The copies are back on sale and the Stripe page says the session expired.
- [ ] **Expiry:** leave a checkout unpaid past `CHECKOUT_HOLD_MINUTES`. `checkout.session.expired` arrives (Stripe can take a few minutes) and the copies are back on sale.
- [ ] **Dispute:** pay with `4000 0000 0000 0259`. The checkout's orders become `disputed`.
- [ ] **Redelivery:** resend a `checkout.session.completed` event from the dashboard (Developers → Events → Resend). It returns 200 and changes nothing.

Deployed (test mode):

- [ ] **Migration:** the Deploy run's "Migrate database" step ends with `alembic current` showing the newest migration (`0009` now).
- [ ] **Webhook endpoint and secrets:** created and stored as in "Deployed webhook", then redeployed.
- [ ] **One real purchase:** repeat seller setup, paying and shipping against the Cloud Run URL. The endpoint's deliveries in the Stripe dashboard all show 200.

## Before launch

Decisions and switches to settle before taking real money:

- [ ] **Fees:** `PLATFORM_FEE_PERCENT`, `PLATFORM_FEE_FIXED_CENTS`, `PLATFORM_FEE_MIN_CENTS` (placeholder: 5%, no fixed fee, no minimum). Stripe's own fees come out of the platform's share (2.9% + 30¢ per checkout, plus Connect's per-payout and monthly active-account fees), so check that cheap orders still cover them.
- [ ] **Payout timing:** `PAYOUT_ON` (`shipped`, `paid` or `delivered`). `delivered` protects buyers best (refunds never need clawing back) but makes sellers wait up to `AUTO_DELIVER_DAYS`. Also decide what happens when an order is never shipped (auto-refund after N days isn't built).
- [ ] **Hold time:** `CHECKOUT_HOLD_MINUTES` (30 to 1440).
- [ ] **Refunds and disputes:** issues handle refunds after payment (see "Order issues"); buyers still can't cancel on their own, and bank disputes (chargebacks) are only flagged, to be answered in the Stripe dashboard. Decide the issue windows (`ISSUE_WINDOW_DAYS`, `ISSUE_SELLER_DAYS`, `AUTO_DELIVER_DAYS`), whether the platform should keep its fee on refunds (today it gives it up on a full refund), and whether to handle `charge.dispute.closed`.
- [ ] **Moderators:** set `is_admin` for whoever will moderate ("Moderation"), and publish rules for what gets listings removed and accounts suspended.
- [ ] **Sales tax:** check marketplace facilitator rules for where you sell (Stripe Tax or a provider).
- [ ] **Stripe account:** activate live mode (business details, bank account), set Connect branding, and publish terms of service and a refund policy (Stripe asks for their URLs).
- [ ] **Live keys:** store the live secret key and a live webhook endpoint's secret (`deploy/cloudrun-setup.sh`), configured with the events listed above.
- [ ] **Frontend:** set `FRONTEND_URL` and `CORS_ORIGINS` in `deploy/cloudrun.env.yaml` to the real web app.
- [ ] **API docs:** decide whether `/docs` stays public.
- [ ] **Backups:** Supabase's free plan has no point-in-time recovery; schedule `pg_dump` backups once real orders exist.
- [ ] **Price factors:** `CONDITION_PRICE_FACTORS`, `MARKET_PRICE_SALES`, `MARKET_PRICE_MIN_SALES` and `MARKET_PRICE_DAYS` (placeholders). Change their defaults in `src/utils/settings.py`: the nightly job (GitHub Actions) and the API (suggested prices) both use them, so environment variables would have to be set in both places.
- [ ] **Price manipulation:** a seller can buy their own listings from a second account to push a market price up. The median of several sales limits this; before launch, consider ignoring sales between accounts that share a payment card or address, and watching for sellers with many sales to the same buyer.
- [ ] **Scan photos:** set up the bucket ("Photo bucket"), add the real frontend to its CORS policy, and decide how long photos are kept (the lifecycle rule).
- [ ] **Hard scans:** an optional paid fallback (sending the photo to a vision model when OCR finds nothing) isn't built; decide whether it's worth the per-scan cost.
- [ ] **Emails:** verify a sending domain in Resend and turn emails on ("Emails"). Review the template wording, and check the `emails` table for `failed` rows after launch.
- [ ] **Webhook monitoring:** if webhooks fail, held copies stay held until Stripe stops retrying (3 days). Watch the endpoint's delivery status in the Stripe dashboard (and Sentry).
