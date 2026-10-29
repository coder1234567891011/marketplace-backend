from httpx import AsyncClient

from conftest import as_user

ADDRESS = {"line1": "1 Main St", "city": "Richmond", "state": "VA", "postal_code": "23220", "country": "US"}


async def test_addresses_are_private(client: AsyncClient, make_user) -> None:
    alice, mallory = await make_user(), await make_user()
    created = await client.post("/v1/addresses", headers=as_user(alice), json=ADDRESS)
    assert created.status_code == 201
    address_id = created.json()["id"]

    assert (await client.get(f"/v1/addresses/{address_id}", headers=as_user(mallory))).status_code == 404
    assert (await client.put(f"/v1/addresses/{address_id}", headers=as_user(mallory),
                             json=ADDRESS)).status_code == 404
    assert (await client.delete(f"/v1/addresses/{address_id}", headers=as_user(mallory))).status_code == 404
    assert (await client.get("/v1/addresses", headers=as_user(mallory))).json() == []

    mine = await client.get("/v1/addresses", headers=as_user(alice))
    assert [a["id"] for a in mine.json()] == [address_id]
    assert (await client.delete(f"/v1/addresses/{address_id}", headers=as_user(alice))).status_code == 204


async def test_only_one_default_address(client: AsyncClient, make_user) -> None:
    alice = await make_user()
    first = (await client.post("/v1/addresses", headers=as_user(alice), json={**ADDRESS, "is_default": True})).json()
    second = (await client.post("/v1/addresses", headers=as_user(alice), json={**ADDRESS, "is_default": True})).json()
    defaults = {a["id"]: a["is_default"] for a in (await client.get("/v1/addresses", headers=as_user(alice))).json()}
    assert defaults == {first["id"]: False, second["id"]: True}
