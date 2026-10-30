from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    environment: str = "development"

    supabase_project_ref: str = ""
    # Postgres connection string. Supabase gives postgres://… URLs; they are
    # normalized to the asyncpg driver in services/persistence/database.py.
    database_url: str = ""
    db_pool_size: int = 5

    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1

    scryfall_user_agent: str = ""

    # Comma-separated browser origins allowed to call the API. Native app
    # requests send no Origin header and aren't affected by CORS.
    cors_origins: str = "http://localhost:8081,http://127.0.0.1:8081"

    # Where the real client IP comes from (used by the per-IP rate limit):
    #   "socket"          - the TCP peer address (local dev, no proxy)
    #   "fly"             - the Fly-Client-IP header set by Fly's proxy
    #   "x-forwarded-for" - X-Forwarded-For, counting forwarded_for_hops entries
    #                       from the right (Cloud Run: 1; behind a Google load
    #                       balancer: 2). Entries further left are client-supplied.
    client_ip_source: Literal["socket", "fly", "x-forwarded-for"] = "socket"
    forwarded_for_hops: int = 1

    rate_limit_enabled: bool = True
    # Requests per minute from one IP address, across all endpoints.
    rate_limit_per_ip_per_minute: int = 300

    # Stripe (test keys until launch). The webhook secret is per endpoint:
    # `stripe listen` prints one for local testing; the dashboard endpoint has its own.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # The web app: checkout and seller onboarding send people back here.
    frontend_url: str = "http://localhost:8081"

    # Marketplace terms. Placeholders, to be finalized before launch. Each
    # order records the fee terms it was charged under, so changing these
    # only affects new orders.
    # Fee per seller order: percent of the cards' price (not shipping) plus a
    # fixed amount, at least platform_fee_min_cents.
    platform_fee_percent: Decimal = Field(default=Decimal("5"), ge=0, le=50)
    platform_fee_fixed_cents: int = Field(default=0, ge=0)
    platform_fee_min_cents: int = Field(default=0, ge=0)
    # How long checkout holds the copies; Stripe allows 30 minutes to 24 hours.
    checkout_hold_minutes: int = Field(default=30, ge=30, le=1440)
    # When sellers are paid: when they mark the order shipped (protects
    # buyers), or as soon as the buyer pays.
    payout_on: Literal["shipped", "paid"] = "shipped"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
