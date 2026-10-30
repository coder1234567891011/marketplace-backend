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
| `MARKET_PRICE_DAYS`, `MARKET_PRICE_SALES`, `MARKET_PRICE_MIN_SALES`, `CONDITION_PRICE_FACTORS` | How market prices are worked out, see "Market prices" |

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
- **Orders:** sellers `POST /v1/orders/{id}/ship` (optional `tracking_number`) or `/refund` (full refund of an unshipped order); buyers `POST /v1/orders/{id}/received`, which allows ratings. Sellers only see orders once paid. A dispute webhook marks the checkout's orders `disputed`.
- **Fees and payouts:** each order records its fee terms, the `platform_fee_cents` and the `seller_payout_cents` (total minus fee). The seller is paid when they ship (`PAYOUT_ON=shipped`) or when the buyer pays (`PAYOUT_ON=paid`); a refund reverses the payout first. Terms are settings (`PLATFORM_FEE_PERCENT`, `PLATFORM_FEE_FIXED_CENTS`, `PLATFORM_FEE_MIN_CENTS`, `CHECKOUT_HOLD_MINUTES`, `PAYOUT_ON` in `deploy/cloudrun.env.yaml`); the current values are placeholders.

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

## Market prices

Prices come from sales on the marketplace. Scryfall's TCGplayer and Cardmarket prices are only shown as `reference_prices`.

- **Sales:** order items of paid, shipped or delivered orders, keyed by card, finish, condition and language. Refunded, cancelled and disputed orders don't count. `GET /v1/cards/{id}/sales` shows them publicly (price, condition, date, never who bought or sold).
- **Market price:** one per card, finish and language, in NM condition. Each sale is scaled to NM with `CONDITION_PRICE_FACTORS` (placeholder: LP 0.9, MP 0.75, HP 0.6, DMG 0.4), then the price is the median of the last `MARKET_PRICE_SALES` (10) sales within `MARKET_PRICE_DAYS` (90), once there are at least `MARKET_PRICE_MIN_SALES` (3). Until then it is the cheapest NM listing for sale, marked `source: "listings"`.
- **Nightly job:** `src/scripts/market_prices.py` rebuilds `market_prices`, as the second step of the Scryfall sync workflow (it runs even if the sync fails). A sale shows up in the market price the next night. Run it by hand with `cd src && ../.venv/bin/python -m scripts.market_prices`.
- **History:** `price_snapshots` only gets a row on days a price changed, which keeps it small; the price on any day is the latest row on or before it. `price-history` starts with the price in force when the period began.
- **Suggested price:** the market price scaled to the listing's condition. With no market price yet, Scryfall's TCGplayer price for that finish, scaled the same way and marked `source: "reference"`, but only in the printing's own language.

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
- [ ] **Cancel:** `POST /v1/checkout/{id}/cancel`. The copies are back on sale and the Stripe page says the session expired.
- [ ] **Expiry:** leave a checkout unpaid past `CHECKOUT_HOLD_MINUTES`. `checkout.session.expired` arrives (Stripe can take a few minutes) and the copies are back on sale.
- [ ] **Dispute:** pay with `4000 0000 0000 0259`. The checkout's orders become `disputed`.
- [ ] **Redelivery:** resend a `checkout.session.completed` event from the dashboard (Developers → Events → Resend). It returns 200 and changes nothing.

Deployed (test mode):

- [ ] **Migration:** `alembic upgrade head` against production, then `alembic current` shows `0005`.
- [ ] **Webhook endpoint and secrets:** created and stored as in "Deployed webhook", then redeployed.
- [ ] **One real purchase:** repeat seller setup, paying and shipping against the Cloud Run URL. The endpoint's deliveries in the Stripe dashboard all show 200.

## Before launch

Decisions and switches to settle before taking real money:

- [ ] **Fees:** `PLATFORM_FEE_PERCENT`, `PLATFORM_FEE_FIXED_CENTS`, `PLATFORM_FEE_MIN_CENTS` (placeholder: 5%, no fixed fee, no minimum). Stripe's own fees come out of the platform's share (2.9% + 30¢ per checkout, plus Connect's per-payout and monthly active-account fees), so check that cheap orders still cover them.
- [ ] **Payout timing:** `PAYOUT_ON` (`shipped` or `paid`). If sellers are paid on shipping, decide what happens when an order is never shipped (auto-refund after N days).
- [ ] **Hold time:** `CHECKOUT_HOLD_MINUTES` (30 to 1440).
- [ ] **Refunds and disputes:** today sellers can fully refund unshipped orders, buyers can't cancel, and disputes are only flagged. Decide on buyer cancellation, partial refunds, refunds after shipping, and who resolves disputes.
- [ ] **Delivery:** buyers confirm delivery by hand; consider confirming automatically some days after shipping so ratings unlock.
- [ ] **Sales tax:** check marketplace facilitator rules for where you sell (Stripe Tax or a provider).
- [ ] **Stripe account:** activate live mode (business details, bank account), set Connect branding, and publish terms of service and a refund policy (Stripe asks for their URLs).
- [ ] **Live keys:** store the live secret key and a live webhook endpoint's secret (`deploy/cloudrun-setup.sh`), configured with the events listed above.
- [ ] **Frontend:** set `FRONTEND_URL` and `CORS_ORIGINS` in `deploy/cloudrun.env.yaml` to the real web app.
- [ ] **API docs:** decide whether `/docs` stays public.
- [ ] **Backups:** Supabase's free plan has no point-in-time recovery; schedule `pg_dump` backups once real orders exist.
- [ ] **Price factors:** `CONDITION_PRICE_FACTORS`, `MARKET_PRICE_SALES`, `MARKET_PRICE_MIN_SALES` and `MARKET_PRICE_DAYS` (placeholders). Change their defaults in `src/utils/settings.py`: the nightly job (GitHub Actions) and the API (suggested prices) both use them, so environment variables would have to be set in both places.
- [ ] **Price manipulation:** a seller can buy their own listings from a second account to push a market price up. The median of several sales limits this; before launch, consider ignoring sales between accounts that share a payment card or address, and watching for sellers with many sales to the same buyer.
- [ ] **Emails:** order confirmation, "you sold something" and "your order shipped" emails aren't built yet (planned with Resend).
- [ ] **Webhook monitoring:** if webhooks fail, held copies stay held until Stripe stops retrying (3 days). Watch the endpoint's delivery status in the Stripe dashboard (and Sentry).
