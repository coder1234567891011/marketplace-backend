"""
Async SQLAlchemy engine and the per-request session dependency.

Each request gets one AsyncSession inside one transaction: it commits when
the route returns and rolls back if anything raises. Services only flush.
"""
import uuid
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from utils.settings import settings


def normalize_database_url(raw_url: str) -> tuple[str, dict[str, Any]]:
    """Turn a Supabase/libpq URL into an asyncpg URL plus connect args."""
    if not raw_url.startswith(("postgres://", "postgresql://", "postgresql+")):
        raise RuntimeError(
            "DATABASE_URL must be a Postgres connection string (postgresql://user:pass@host:port/db), "
            "e.g. Supabase → Connect → Session pooler; not a dashboard or project https:// URL"
        )
    url = make_url(raw_url)
    if url.drivername in ("postgres", "postgresql", "postgresql+psycopg2", "postgresql+psycopg"):
        url = url.set(drivername="postgresql+asyncpg")
    connect_args: dict[str, Any] = {
        # Supabase's pooler (Supavisor) in transaction mode can't keep named
        # prepared statements across connections; unique names + no cache work
        # with both session and transaction mode.
        "statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4()}__",
    }
    query = dict(url.query)
    if "sslmode" in query:
        connect_args["ssl"] = query.pop("sslmode")
        url = url.set(query=query)
    return url.render_as_string(hide_password=False), connect_args


def create_engine(raw_url: str, **kwargs: Any) -> AsyncEngine:
    url, connect_args = normalize_database_url(raw_url)
    return create_async_engine(url, connect_args=connect_args, pool_pre_ping=True, **kwargs)


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _engine, _sessionmaker
    if _sessionmaker is None:
        if not settings.database_url:
            raise RuntimeError("DATABASE_URL is not configured")
        _engine = create_engine(settings.database_url, pool_size=settings.db_pool_size, max_overflow=5)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session, session.begin():
        yield session


# scope="function": commit before the response is sent, so a failed commit
# becomes an error response instead of a silently lost write.
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
