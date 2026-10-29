from pathlib import Path

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

    rate_limit_enabled: bool = True
    # Requests per minute from one IP address, across all endpoints.
    rate_limit_per_ip_per_minute: int = 300

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
