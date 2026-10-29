import uuid

from httpx import AsyncClient

from conftest import as_user


async def test_private_collections_are_hidden(client: AsyncClient, make_user, make_card) -> None:
    alice, bob = await make_user(), await make_user()
    card_id = await make_card()
    private = (await client.post("/v1/collections", headers=as_user(alice), json={"name": "Binder"})).json()
    public = (await client.post("/v1/collections", headers=as_user(alice),
                                json={"name": "Trade binder", "is_public": True})).json()
    items = await client.post(f"/v1/collections/{private['id']}/items", headers=as_user(alice),
                              json=[{"card_id": str(card_id), "quantity": 4, "condition": "NM"}])
    assert items.status_code == 201, items.text
    assert items.json()[0]["card"]["name"] == "Lightning Bolt"
    item_id = items.json()[0]["id"]

    seen_by_bob = (await client.get(f"/v1/collections/user/{alice}", headers=as_user(bob))).json()
    assert [c["id"] for c in seen_by_bob] == [public["id"]]
    assert len((await client.get(f"/v1/collections/user/{alice}", headers=as_user(alice))).json()) == 2

    assert (await client.get(f"/v1/collections/{private['id']}")).status_code == 404
    assert (await client.get(f"/v1/collections/{private['id']}/items", headers=as_user(bob))).status_code == 404
    assert (await client.get(f"/v1/collections/items/{item_id}", headers=as_user(bob))).status_code == 404
    assert (await client.get(f"/v1/collections/{public['id']}")).status_code == 200


async def test_others_cannot_modify_collection(client: AsyncClient, make_user, make_card) -> None:
    alice, bob = await make_user(), await make_user()
    card_id = await make_card()
    coll = (await client.post("/v1/collections", headers=as_user(alice), json={"is_public": True})).json()
    item = (await client.post(f"/v1/collections/{coll['id']}/items", headers=as_user(alice),
                              json=[{"card_id": str(card_id)}])).json()[0]

    assert (await client.patch(f"/v1/collections/{coll['id']}", headers=as_user(bob),
                               json={"name": "mine now"})).status_code == 404
    assert (await client.delete(f"/v1/collections/{coll['id']}", headers=as_user(bob))).status_code == 404
    assert (await client.post(f"/v1/collections/{coll['id']}/items", headers=as_user(bob),
                              json=[{"card_id": str(card_id)}])).status_code == 404
    assert (await client.patch(f"/v1/collections/items/{item['id']}", headers=as_user(bob),
                               json={"quantity": 99})).status_code == 404
    assert (await client.delete(f"/v1/collections/items/{item['id']}", headers=as_user(bob))).status_code == 404

    edited = await client.patch(f"/v1/collections/items/{item['id']}", headers=as_user(alice), json={"quantity": 3})
    assert edited.json()["quantity"] == 3


async def test_unknown_card_is_rejected(client: AsyncClient, make_user) -> None:
    alice = await make_user()
    coll = (await client.post("/v1/collections", headers=as_user(alice), json={})).json()
    resp = await client.post(f"/v1/collections/{coll['id']}/items", headers=as_user(alice),
                             json=[{"card_id": str(uuid.uuid4())}])
    assert resp.status_code == 404
    assert resp.json()["error"]["message"].startswith("Card not found")


async def test_items_paginate(client: AsyncClient, make_user, make_card) -> None:
    alice = await make_user()
    coll = (await client.post("/v1/collections", headers=as_user(alice), json={})).json()
    cards = [await make_card(f"Card {i}") for i in range(5)]
    await client.post(f"/v1/collections/{coll['id']}/items", headers=as_user(alice),
                      json=[{"card_id": str(c)} for c in cards])

    seen, cursor = [], None
    for _ in range(5):
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = (await client.get(f"/v1/collections/{coll['id']}/items", headers=as_user(alice), params=params)).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == 5 and len(set(seen)) == 5

    bad = await client.get(f"/v1/collections/{coll['id']}/items", headers=as_user(alice), params={"cursor": "junk"})
    assert bad.status_code == 400
