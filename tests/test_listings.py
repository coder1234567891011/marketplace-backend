import uuid

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
    assert resp.status_code == 409
    await _listing(client, alice, bolt, collection_item_id=item["id"], quantity_available=2)


async def test_validation_error_shape(client: AsyncClient, make_user, make_card) -> None:
    alice = await make_user()
    resp = await client.post("/v1/listings", headers=as_user(alice),
                             json={"card_id": str(await make_card()), "condition": "NM", "price_cents": 0})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"
    assert resp.json()["error"]["details"][0]["loc"][-1] == "price_cents"


# ----------------------------------------------------------------
# Reserving collection copies
# ----------------------------------------------------------------
async def _item(client: AsyncClient, user, card_id, quantity: int, **extra) -> dict:
    coll = (await client.post("/v1/collections", headers=as_user(user), json={})).json()
    resp = await client.post(f"/v1/collections/{coll['id']}/items", headers=as_user(user),
                             json=[{"card_id": str(card_id), "quantity": quantity, **extra}])
    assert resp.status_code == 201, resp.text
    return resp.json()[0]


async def test_listings_reserve_collection_copies(client: AsyncClient, make_user, make_card) -> None:
    alice = await make_user()
    bolt = await make_card()
    item = await _item(client, alice, bolt, 4, condition="NM", finish="foil", language="ja")

    first = await _listing(client, alice, bolt, collection_item_id=item["id"], quantity_available=3)
    assert (first["finish"], first["language"]) == ("foil", "ja")  # taken from the item
    resp = await client.post("/v1/listings", headers=as_user(alice), json={
        "card_id": str(bolt), "collection_item_id": item["id"], "condition": "NM", "price_cents": 100,
        "quantity_available": 2})
    assert resp.status_code == 409 and "Only 1" in resp.json()["error"]["message"]
    resp = await client.post("/v1/listings", headers=as_user(alice), json={
        "card_id": str(bolt), "collection_item_id": item["id"], "condition": "LP", "price_cents": 100})
    assert resp.status_code == 400

    item_read = (await client.get(f"/v1/collections/items/{item['id']}", headers=as_user(alice))).json()
    assert item_read["quantity_listed"] == 3

    # Raising the listing beyond the item, or lowering the item below the listing, is refused.
    assert (await client.patch(f"/v1/listings/{first['id']}", headers=as_user(alice),
                               json={"quantity_available": 5})).status_code == 409
    assert (await client.patch(f"/v1/listings/{first['id']}", headers=as_user(alice),
                               json={"quantity_available": 4})).status_code == 200
    assert (await client.patch(f"/v1/collections/items/{item['id']}", headers=as_user(alice),
                               json={"quantity": 3})).status_code == 409
    assert (await client.patch(f"/v1/collections/items/{item['id']}", headers=as_user(alice),
                               json={"finish": "nonfoil"})).status_code == 409
    assert (await client.delete(f"/v1/collections/items/{item['id']}", headers=as_user(alice))).status_code == 409

    # Removing the listing frees the copies; the removed listing keeps its history.
    assert (await client.delete(f"/v1/listings/{first['id']}", headers=as_user(alice))).status_code == 204
    assert (await client.patch(f"/v1/collections/items/{item['id']}", headers=as_user(alice),
                               json={"quantity": 1})).status_code == 200
    assert (await client.delete(f"/v1/collections/items/{item['id']}", headers=as_user(alice))).status_code == 204
    removed = (await client.get(f"/v1/listings/{first['id']}")).json()
    assert (removed["status"], removed["collection_item_id"]) == ("removed", None)


async def test_finish_must_exist_for_the_printing(client: AsyncClient, make_user, make_card) -> None:
    alice = await make_user()
    card = await make_card(finishes=["nonfoil"])
    resp = await client.post("/v1/listings", headers=as_user(alice), json={
        "card_id": str(card), "condition": "NM", "price_cents": 100, "finish": "foil"})
    assert resp.status_code == 400 and "isn't printed in foil" in resp.json()["error"]["message"]
    coll = (await client.post("/v1/collections", headers=as_user(alice), json={})).json()
    resp = await client.post(f"/v1/collections/{coll['id']}/items", headers=as_user(alice),
                             json=[{"card_id": str(card), "finish": "etched"}])
    assert resp.status_code == 400


def test_status_transitions() -> None:
    import pytest

    from models.database import Listing, ListingStatus
    from services.listings.listings import change_status
    from utils.errors import Conflict

    listing = Listing(status=ListingStatus.active)
    change_status(listing, ListingStatus.pending_sale)
    change_status(listing, ListingStatus.active)
    change_status(listing, ListingStatus.pending_sale)
    change_status(listing, ListingStatus.sold)
    for status in ListingStatus:
        with pytest.raises(Conflict):
            change_status(listing, status)
    with pytest.raises(Conflict):
        change_status(Listing(status=ListingStatus.active), ListingStatus.sold)


# ----------------------------------------------------------------
# Browsing
# ----------------------------------------------------------------
async def test_card_listings_cheapest_first_and_summary(
    client: AsyncClient, db, make_user, make_card
) -> None:
    from sqlalchemy import update

    from models.database import AccountStatus, User

    alice, bob, banned = await make_user(), await make_user(), await make_user()
    bolt = await make_card()
    a1 = await _listing(client, alice, bolt, price_cents=300, quantity_available=2)
    b1 = await _listing(client, bob, bolt, price_cents=150, condition="LP")
    b2 = await _listing(client, bob, bolt, price_cents=500, finish="foil")
    await _listing(client, banned, bolt, price_cents=1)
    await db.execute(update(User).where(User.id == banned).values(account_status=AccountStatus.banned))

    resp = (await client.get(f"/v1/listings/card/{bolt}", params={"limit": 2})).json()
    rest = (await client.get(f"/v1/listings/card/{bolt}", params={"limit": 2, "cursor": resp["next_cursor"]})).json()
    assert [lst["id"] for lst in resp["items"] + rest["items"]] == [b1["id"], a1["id"], b2["id"]]
    foil = (await client.get(f"/v1/listings/card/{bolt}", params={"finish": "foil"})).json()["items"]
    assert [lst["id"] for lst in foil] == [b2["id"]]

    summary = (await client.get(f"/v1/listings/card/{bolt}/summary")).json()
    assert summary == [
        {"condition": "LP", "finish": "nonfoil", "listings": 1, "quantity": 1, "lowest_price_cents": 150},
        {"condition": "NM", "finish": "nonfoil", "listings": 1, "quantity": 2, "lowest_price_cents": 300},
        {"condition": "NM", "finish": "foil", "listings": 1, "quantity": 1, "lowest_price_cents": 500},
    ]
    assert (await client.get(f"/v1/listings/card/{uuid.uuid4()}/summary")).status_code == 404


async def test_storefront_search_and_sort(client: AsyncClient, make_user, make_card) -> None:
    alice = await make_user()
    bolt, counter = await make_card(), await make_card("Counterspell")
    cheap = await _listing(client, alice, bolt, price_cents=100)
    pricey = await _listing(client, alice, counter, price_cents=900, notes="Signed by the artist")
    assert pricey["notes"] == "Signed by the artist"

    async def ids(**params) -> list[str]:
        resp = await client.get(f"/v1/listings/user/{alice}", params=params)
        assert resp.status_code == 200, resp.text
        return [lst["id"] for lst in resp.json()["items"]]

    # Both are created in one test transaction, so they share created_at.
    assert sorted(await ids()) == sorted([pricey["id"], cheap["id"]])
    assert await ids(sort="price_asc") == [cheap["id"], pricey["id"]]
    assert await ids(sort="price_desc") == [pricey["id"], cheap["id"]]
    assert await ids(q="counter") == [pricey["id"]]
