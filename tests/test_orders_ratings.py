import uuid

from httpx import AsyncClient

from conftest import as_user
from models.database import OrderStatus


async def test_clients_cannot_create_or_edit_orders(client: AsyncClient, make_user, make_order) -> None:
    buyer, seller = await make_user(), await make_user()
    order_id = await make_order(buyer, seller, OrderStatus.paid)
    assert (await client.post("/v1/orders", headers=as_user(buyer), json={})).status_code == 405
    assert (await client.put(f"/v1/orders/{order_id}", headers=as_user(buyer), json={})).status_code == 405
    assert (await client.delete(f"/v1/orders/{order_id}", headers=as_user(buyer))).status_code == 405


async def test_orders_visible_only_to_parties(client: AsyncClient, make_user, make_order) -> None:
    buyer, seller, stranger = await make_user(), await make_user(), await make_user()
    order_id = await make_order(buyer, seller, OrderStatus.paid)

    assert (await client.get(f"/v1/orders/{order_id}", headers=as_user(buyer))).status_code == 200
    assert (await client.get(f"/v1/orders/{order_id}", headers=as_user(seller))).status_code == 200
    assert (await client.get(f"/v1/orders/{order_id}", headers=as_user(stranger))).status_code == 404

    as_buyer = (await client.get("/v1/orders", headers=as_user(buyer), params={"role": "buyer"})).json()
    as_seller = (await client.get("/v1/orders", headers=as_user(buyer), params={"role": "seller"})).json()
    assert [o["id"] for o in as_buyer["items"]] == [str(order_id)]
    assert as_seller["items"] == []
    bad_role = await client.get("/v1/orders", headers=as_user(buyer), params={"role": "seller_id; drop"})
    assert bad_role.status_code == 422


async def test_rating_rules_and_stats(client: AsyncClient, make_user, make_order) -> None:
    buyer, seller, stranger = await make_user(), await make_user(), await make_user()
    paid = await make_order(buyer, seller, OrderStatus.paid)
    delivered = await make_order(buyer, seller, OrderStatus.delivered)

    assert (await client.post("/v1/ratings", headers=as_user(buyer),
                              json={"order_id": str(paid), "rating": 5})).status_code == 400
    assert (await client.post("/v1/ratings", headers=as_user(stranger),
                              json={"order_id": str(delivered), "rating": 1})).status_code == 404
    assert (await client.post("/v1/ratings", headers=as_user(buyer),
                              json={"order_id": str(uuid.uuid4()), "rating": 5})).status_code == 404

    rated = await client.post("/v1/ratings", headers=as_user(buyer), json={"order_id": str(delivered), "rating": 4})
    assert rated.status_code == 201, rated.text
    assert rated.json()["ratee_id"] == str(seller)
    assert rated.json()["role"] == "seller"
    assert (await client.post("/v1/ratings", headers=as_user(buyer),
                              json={"order_id": str(delivered), "rating": 5})).status_code == 409

    back = await client.post("/v1/ratings", headers=as_user(seller), json={"order_id": str(delivered), "rating": 5})
    assert back.json()["role"] == "buyer"

    seller_profile = (await client.get(f"/v1/users/{seller}")).json()
    assert (seller_profile["seller_rating_avg"], seller_profile["seller_rating_count"]) == (4.0, 1)
    buyer_profile = (await client.get(f"/v1/users/{buyer}")).json()
    assert (buyer_profile["buyer_rating_avg"], buyer_profile["buyer_rating_count"]) == (5.0, 1)

    listed = (await client.get(f"/v1/ratings/user/{seller}")).json()["items"]
    assert [r["rating"] for r in listed] == [4]
