from httpx import AsyncClient

from conftest import as_user


async def _listing(client: AsyncClient, user, card_id, **extra) -> dict:
    resp = await client.post("/v1/listings", headers=as_user(user),
                             json={"card_id": str(card_id), "condition": "NM", "price_cents": 250, **extra})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_create_and_read_listing(client: AsyncClient, make_user, make_card) -> None:
    alice = await make_user("alice")
    listing = await _listing(client, alice, await make_card())
    assert listing["seller"]["username"] == "alice"
    assert listing["card"]["name"] == "Lightning Bolt"

    public = await client.get(f"/v1/listings/{listing['id']}")
    assert public.status_code == 200


async def test_only_seller_can_edit_or_remove(client: AsyncClient, make_user, make_card) -> None:
    alice, bob = await make_user(), await make_user()
    listing = await _listing(client, alice, await make_card())

    assert (await client.patch(f"/v1/listings/{listing['id']}", headers=as_user(bob),
                               json={"price_cents": 1})).status_code == 403
    assert (await client.delete(f"/v1/listings/{listing['id']}", headers=as_user(bob))).status_code == 403

    edited = await client.patch(f"/v1/listings/{listing['id']}", headers=as_user(alice), json={"price_cents": 300})
    assert edited.json()["price_cents"] == 300
    assert edited.json()["seller"]["id"] == str(alice)


async def test_delete_is_soft_and_hides_from_public(client: AsyncClient, make_user, make_card) -> None:
    alice = await make_user()
    listing = await _listing(client, alice, await make_card())
    assert (await client.delete(f"/v1/listings/{listing['id']}", headers=as_user(alice))).status_code == 204

    assert (await client.get(f"/v1/listings/{listing['id']}")).json()["status"] == "removed"
    assert (await client.get(f"/v1/listings/user/{alice}")).json()["items"] == []
    mine = (await client.get(f"/v1/listings/user/{alice}", headers=as_user(alice))).json()["items"]
    assert [lst["id"] for lst in mine] == [listing["id"]]

    resp = await client.patch(f"/v1/listings/{listing['id']}", headers=as_user(alice), json={"price_cents": 1})
    assert resp.status_code == 409


async def test_collection_item_must_be_own_and_match(client: AsyncClient, make_user, make_card) -> None:
    alice, bob = await make_user(), await make_user()
    bolt, counterspell = await make_card(), await make_card("Counterspell")
    coll = (await client.post("/v1/collections", headers=as_user(alice), json={})).json()
    item = (await client.post(f"/v1/collections/{coll['id']}/items", headers=as_user(alice),
                              json=[{"card_id": str(bolt), "quantity": 2}])).json()[0]

    resp = await client.post("/v1/listings", headers=as_user(bob), json={
        "card_id": str(bolt), "collection_item_id": item["id"], "condition": "NM", "price_cents": 100})
    assert resp.status_code == 404
    resp = await client.post("/v1/listings", headers=as_user(alice), json={
        "card_id": str(counterspell), "collection_item_id": item["id"], "condition": "NM", "price_cents": 100})
    assert resp.status_code == 400
    resp = await client.post("/v1/listings", headers=as_user(alice), json={
        "card_id": str(bolt), "collection_item_id": item["id"], "condition": "NM", "price_cents": 100,
        "quantity_available": 3})
    assert resp.status_code == 400
    await _listing(client, alice, bolt, collection_item_id=item["id"], quantity_available=2)


async def test_validation_error_shape(client: AsyncClient, make_user, make_card) -> None:
    alice = await make_user()
    resp = await client.post("/v1/listings", headers=as_user(alice),
                             json={"card_id": str(await make_card()), "condition": "NM", "price_cents": 0})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert resp.json()["error"]["details"][0]["loc"][-1] == "price_cents"
