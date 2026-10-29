from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy.ext.asyncio import AsyncConnection

from models.database import Base


async def test_migrations_match_models(conn: AsyncConnection) -> None:
    """Fails when a model changes without a matching Alembic migration."""
    def diff(sync_conn):  # type: ignore[no-untyped-def]
        ctx = MigrationContext.configure(sync_conn, opts={"compare_type": True})
        return [d for d in compare_metadata(ctx, Base.metadata)
                if not (d[0] in ("add_table", "remove_table") and getattr(d[1], "schema", None) == "auth")]

    assert await conn.run_sync(diff) == []
