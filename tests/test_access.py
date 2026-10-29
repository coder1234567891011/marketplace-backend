"""Account status, email confirmation, rate limiting and CORS."""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from conftest import add_auth_user, as_user
from models.database import AccountStatus, User
from utils.rate_limit import client_ip, limiter
from utils.settings import settings


async def _set_status(db: AsyncSession, user_id: uuid.UUID, status: AccountStatus) -> None:
    await db.execute(update(User).where(User.id == user_id).values(account_status=status))
    await db.flush()


async def test_unconfirmed_email_is_blocked(client: AsyncClient, db: AsyncSession) -> None:
    user_id = await add_auth_user(db, confirmed=False)
    resp = await client.post("/v1/users/me", headers=as_user(user_id), json={"username": "unconfirmed"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "email_unconfirmed"


async def test_token_for_unknown_auth_user_is_rejected(client: AsyncClient) -> None:
    resp = await client.get("/v1/users/me", headers=as_user(uuid.uuid4()))
    assert resp.status_code == 401


async def test_banned_user_is_blocked_everywhere(client: AsyncClient, db: AsyncSession, make_user) -> None:
    user_id = await make_user()
    await _set_status(db, user_id, AccountStatus.banned)
    for method, path in (("GET", "/v1/users/me"), ("GET", "/v1/addresses"), ("POST", "/v1/collections")):
        resp = await client.request(method, path, headers=as_user(user_id), json={} if method == "POST" else None)
        assert resp.status_code == 403, (method, path)
        assert resp.json()["error"]["code"] == "account_banned"


async def test_suspended_user_can_read_but_not_write(client: AsyncClient, db: AsyncSession, make_user) -> None:
    user_id = await make_user()
    await _set_status(db, user_id, AccountStatus.suspended)
    assert (await client.get("/v1/users/me", headers=as_user(user_id))).status_code == 200
    resp = await client.post("/v1/collections", headers=as_user(user_id), json={})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "account_suspended"


async def test_per_user_write_limit(client: AsyncClient, make_user, make_card) -> None:
    alice, bob = await make_user(), await make_user()
    card = str(await make_card())
    body = {"card_id": card, "condition": "NM", "price_cents": 100}
    limiter.reset()
    for _ in range(20):
        assert (await client.post("/v1/ratings", headers=as_user(alice),
                                  json={"order_id": str(uuid.uuid4()), "rating": 5})).status_code == 404
    limited = await client.post("/v1/ratings", headers=as_user(alice),
                                json={"order_id": str(uuid.uuid4()), "rating": 5})
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert int(limited.headers["Retry-After"]) > 0
    # Limits are per user and per bucket.
    assert (await client.post("/v1/ratings", headers=as_user(bob),
                              json={"order_id": str(uuid.uuid4()), "rating": 5})).status_code == 404
    assert (await client.post("/v1/listings", headers=as_user(alice), json=body)).status_code == 201


async def test_rate_limit_runs_after_auth(client: AsyncClient, make_user) -> None:
    """A forged token naming someone else must not use up their allowance."""
    alice = await make_user()
    limiter.reset()
    for _ in range(25):
        resp = await client.post("/v1/ratings", headers={"Authorization": "Bearer not-a-real-user"},
                                 json={"order_id": str(uuid.uuid4()), "rating": 5})
        assert resp.status_code == 401
    resp = await client.post("/v1/ratings", headers=as_user(alice), json={"order_id": str(uuid.uuid4()), "rating": 5})
    assert resp.status_code == 404


async def test_per_ip_limit(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_per_ip_per_minute", 3)
    monkeypatch.setattr(settings, "client_ip_source", "fly")
    limiter.reset()
    headers = {"Fly-Client-IP": "203.0.113.7"}
    for _ in range(3):
        assert (await client.get(f"/v1/cards/{uuid.uuid4()}", headers=headers)).status_code == 404
    resp = await client.get(f"/v1/cards/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "rate_limited"
    other_ip = await client.get(f"/v1/cards/{uuid.uuid4()}", headers={"Fly-Client-IP": "203.0.113.8"})
    assert other_ip.status_code == 404
    assert (await client.get("/health", headers=headers)).status_code == 200


async def test_bulk_item_cap(client: AsyncClient, make_user) -> None:
    alice = await make_user()
    coll = (await client.post("/v1/collections", headers=as_user(alice), json={})).json()
    items = [{"card_id": str(uuid.uuid4())}] * 501
    resp = await client.post(f"/v1/collections/{coll['id']}/items", headers=as_user(alice), json=items)
    assert resp.status_code == 422


async def test_cors_allows_configured_origins_only(client: AsyncClient) -> None:
    allowed = settings.cors_origin_list[0]
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}
    ok = await client.options("/v1/listings", headers={"Origin": allowed, **preflight})
    assert ok.status_code == 200
    assert ok.headers["access-control-allow-origin"] == allowed

    bad = await client.options("/v1/listings", headers={"Origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in bad.headers


def _scope(xff: str | None = None, fly: str | None = None) -> dict:
    headers = []
    if xff is not None:
        headers.append((b"x-forwarded-for", xff.encode()))
    if fly is not None:
        headers.append((b"fly-client-ip", fly.encode()))
    return {"type": "http", "headers": headers, "client": ("10.0.0.1", 1234)}


@pytest.mark.parametrize("source, hops, scope, expected", [
    # Local: headers are ignored, so they can't be spoofed.
    ("socket", 1, _scope(xff="1.1.1.1", fly="2.2.2.2"), "10.0.0.1"),
    ("fly", 1, _scope(fly="2.2.2.2"), "2.2.2.2"),
    ("fly", 1, _scope(), "10.0.0.1"),
    # Cloud Run appends the real client last; earlier entries are client-supplied.
    ("x-forwarded-for", 1, _scope(xff="6.6.6.6, 3.3.3.3"), "3.3.3.3"),
    ("x-forwarded-for", 1, _scope(xff="3.3.3.3"), "3.3.3.3"),
    # Behind a Google load balancer, the LB's own address is last.
    ("x-forwarded-for", 2, _scope(xff="6.6.6.6, 3.3.3.3, 34.1.1.1"), "3.3.3.3"),
    ("x-forwarded-for", 2, _scope(xff="3.3.3.3"), "10.0.0.1"),
    ("x-forwarded-for", 1, _scope(), "10.0.0.1"),
])
def test_client_ip(monkeypatch: pytest.MonkeyPatch, source: str, hops: int, scope: dict, expected: str) -> None:
    monkeypatch.setattr(settings, "client_ip_source", source)
    monkeypatch.setattr(settings, "forwarded_for_hops", hops)
    assert client_ip(scope) == expected
