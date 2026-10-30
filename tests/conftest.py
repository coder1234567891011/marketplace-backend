"""
Tests run against a real Postgres given by TEST_DATABASE_URL (its contents
are dropped and recreated). Each test runs inside a transaction that is
rolled back afterwards, so tests don't see each other's data.

Authentication is faked: verify_token is patched so that the bearer token
*is* the user id, e.g.  Authorization: Bearer 6f1c…  → CurrentUser(id=6f1c…).
"""
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import insert, text, update
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession
from sqlalchemy.pool import NullPool

import auth.auth
from app import app
from fake_stripe import FakeStripe
from models.database import Card, Order, OrderStatus, User, auth_users
from services.payments import stripe_gateway
from services.persistence.database import create_engine, get_session
from utils.rate_limit import limiter

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "")
SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture(scope="session")
def migrated_db() -> str:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL is not set")
    import asyncio

    async def reset() -> None:
        engine = create_engine(TEST_DATABASE_URL, poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            await conn.execute(text("DROP SCHEMA IF EXISTS auth CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(reset())
    config = Config(str(SRC / "alembic.ini"))
    config.attributes["database_url"] = TEST_DATABASE_URL
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def conn(migrated_db: str) -> AsyncIterator[AsyncConnection]:
    engine = create_engine(migrated_db, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        yield connection
        await transaction.rollback()
    await engine.dispose()


def _session(conn: AsyncConnection) -> AsyncSession:
    return AsyncSession(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)


@pytest.fixture
async def db(conn: AsyncConnection) -> AsyncIterator[AsyncSession]:
    async with _session(conn) as session:
        yield session


@pytest.fixture
async def client(conn: AsyncConnection, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with _session(conn) as session, session.begin():
            yield session

    monkeypatch.setattr(auth.auth, "verify_token", lambda token: {"sub": token})
    limiter.reset()
    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def fake_stripe() -> Iterator[FakeStripe]:
    """Every test talks to an in-memory Stripe, never the real one."""
    fake = FakeStripe()
    stripe_gateway.use_gateway(fake)
    yield fake
    stripe_gateway.use_gateway(None)


def as_user(user_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {user_id}"}


async def add_auth_user(db: AsyncSession, confirmed: bool = True) -> uuid.UUID:
    """A Supabase auth.users row (no marketplace profile yet)."""
    user_id = uuid.uuid4()
    await db.execute(insert(auth_users).values(
        id=user_id, email_confirmed_at=datetime.now(timezone.utc) if confirmed else None))
    await db.flush()
    return user_id


@pytest.fixture
def make_user(client: AsyncClient, db: AsyncSession, fake_stripe: FakeStripe):  # type: ignore[no-untyped-def]
    """Create an auth user plus their marketplace profile; returns the id.
    Users can be paid (finished Stripe onboarding) unless can_sell=False."""
    async def _make(username: str | None = None, can_sell: bool = True) -> uuid.UUID:
        user_id = await add_auth_user(db)
        resp = await client.post("/v1/users/me", json={"username": username or f"u{user_id.hex[:10]}"},
                                 headers=as_user(user_id))
        assert resp.status_code == 201, resp.text
        if can_sell:
            account_id = f"acct_{str(user_id)[:8]}"
            fake_stripe.accounts[account_id] = True
            await db.execute(update(User).where(User.id == user_id).values(
                stripe_connect_account_id=account_id, stripe_connect_onboarded=True))
            await db.flush()
        return user_id
    return _make


@pytest.fixture
def make_card(db: AsyncSession):  # type: ignore[no-untyped-def]
    async def _make(name: str = "Lightning Bolt", **fields: Any) -> uuid.UUID:
        card = Card(**{
            "scryfall_id": uuid.uuid4(), "oracle_id": uuid.uuid4(), "name": name, "set_code": "m10",
            "set_name": "Magic 2010", "collector_number": str(uuid.uuid4().int % 100000), "rarity": "common",
            "layout": "normal", "released_at": date(2009, 7, 17), "finishes": ["nonfoil", "foil"], **fields})
        db.add(card)
        await db.flush()
        return card.id
    return _make


@pytest.fixture
def make_order(db: AsyncSession):  # type: ignore[no-untyped-def]
    async def _make(buyer_id: uuid.UUID, seller_id: uuid.UUID, status: OrderStatus) -> uuid.UUID:
        order = Order(buyer_id=buyer_id, seller_id=seller_id, subtotal_cents=500, total_cents=500, status=status)
        db.add(order)
        await db.flush()
        return order.id
    return _make
