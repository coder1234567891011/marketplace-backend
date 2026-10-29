import uuid

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from conftest import add_auth_user, as_user


async def test_profile_create_ignores_server_owned_fields(client: AsyncClient, db: AsyncSession) -> None:
    user_id = await add_auth_user(db)
    resp = await client.post("/v1/users/me", headers=as_user(user_id), json={
        "username": "bolt_fan", "seller_rating_avg": 5, "total_sales": 999, "account_status": "active",
    })
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["id"] == str(user_id)
    assert body["total_sales"] == 0
    assert body["seller_rating_avg"] is None


async def test_duplicate_profile_and_username(client: AsyncClient, make_user, db: AsyncSession) -> None:
    user_id = await make_user("taken_name")
    resp = await client.post("/v1/users/me", headers=as_user(user_id), json={"username": "another"})
    assert resp.status_code == 409

    other = await add_auth_user(db)
    resp = await client.post("/v1/users/me", headers=as_user(other), json={"username": "taken_name"})
    assert resp.status_code == 409
    assert resp.json()["error"]["message"] == "Username is taken"


async def test_update_me_and_public_profile(client: AsyncClient, make_user) -> None:
    user_id = await make_user()
    resp = await client.patch("/v1/users/me", headers=as_user(user_id), json={"bio": "Modern player"})
    assert resp.status_code == 200
    assert resp.json()["bio"] == "Modern player"

    public = await client.get(f"/v1/users/{user_id}")
    assert public.status_code == 200
    assert public.json()["bio"] == "Modern player"
    assert "stripe_customer_id" not in public.json()


async def test_unknown_user_is_404(client: AsyncClient) -> None:
    resp = await client.get(f"/v1/users/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json() == {"error": {"code": "not_found", "message": "User not found"}}


async def test_actions_require_a_profile(client: AsyncClient, db: AsyncSession) -> None:
    no_profile = await add_auth_user(db)
    resp = await client.post("/v1/collections", headers=as_user(no_profile), json={})
    assert resp.status_code == 403
