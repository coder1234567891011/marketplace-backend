import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from models.database import Base, TextEnum
from services.persistence.database import create_engine
from utils.settings import settings

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    # Tests (and one-off scripts) can pass a URL explicitly; otherwise use settings.
    url = config.attributes.get("database_url") or settings.database_url
    if not url:
        raise RuntimeError("DATABASE_URL is not configured")
    return url


def include_object(obj, name, type_, reflected, compare_to):  # type: ignore[no-untyped-def]
    # auth.* belongs to Supabase; never autogenerate changes for it.
    if type_ == "table" and getattr(obj, "schema", None) == "auth":
        return False
    return True


def render_item(type_, obj, autogen_context):  # type: ignore[no-untyped-def]
    # Migrations shouldn't import app code: TextEnum columns are plain TEXT.
    if type_ == "type" and isinstance(obj, TextEnum):
        return "sa.Text()"
    return False


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      include_object=include_object, render_item=render_item, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline (--sql) migrations are not supported")

asyncio.run(run_async_migrations())
