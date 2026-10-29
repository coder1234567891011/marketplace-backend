from httpx import AsyncClient

from conftest import as_user


async def test_shipping_rates(client: AsyncClient, make_user) -> None:
    alice, bob = await make_user(), await make_user()
    tracked = await client.post("/v1/shipping-rates", headers=as_user(alice),
                                json={"name": "Tracked", "price_cents": 500, "tracked": True})
    assert tracked.status_code == 201, tracked.text
    envelope = (await client.post("/v1/shipping-rates", headers=as_user(alice),
                                  json={"name": "Untracked envelope", "price_cents": 100})).json()

    public = (await client.get(f"/v1/shipping-rates/user/{alice}")).json()
    assert [r["name"] for r in public] == ["Untracked envelope", "Tracked"]

    assert (await client.patch(f"/v1/shipping-rates/{envelope['id']}", headers=as_user(bob),
                               json={"price_cents": 0})).status_code == 404
    edited = await client.patch(f"/v1/shipping-rates/{envelope['id']}", headers=as_user(alice),
                                json={"price_cents": 150})
    assert edited.json()["price_cents"] == 150
    assert (await client.delete(f"/v1/shipping-rates/{envelope['id']}", headers=as_user(bob))).status_code == 404
    assert (await client.delete(f"/v1/shipping-rates/{envelope['id']}", headers=as_user(alice))).status_code == 204
    assert len((await client.get(f"/v1/shipping-rates/user/{alice}")).json()) == 1

    assert (await client.post("/v1/shipping-rates", headers=as_user(alice),
                              json={"name": "Bad", "price_cents": -1})).status_code == 422


async def test_shipping_rate_limit_per_seller(client: AsyncClient, make_user) -> None:
    alice = await make_user()
    for n in range(10):
        resp = await client.post("/v1/shipping-rates", headers=as_user(alice), json={"name": f"R{n}", "price_cents": n})
        assert resp.status_code == 201
    resp = await client.post("/v1/shipping-rates", headers=as_user(alice), json={"name": "R11", "price_cents": 1})
    assert resp.status_code == 409
