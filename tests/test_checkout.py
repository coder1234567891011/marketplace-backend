import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from conftest import as_user
from fake_stripe import FakeStripe
from models.database import Checkout
from services.checkout.fees import FeeTerms
from utils.settings import settings


# ----------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------
async def _rate(client: AsyncClient, seller: uuid.UUID, price_cents: int = 100, name: str = "Envelope") -> str:
    resp = await client.post("/v1/shipping-rates", headers=as_user(seller),
                             json={"name": name, "price_cents": price_cents})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _listing(client: AsyncClient, seller: uuid.UUID, card: uuid.UUID, price_cents: int,
                   quantity: int = 1, **fields: Any) -> str:
    resp = await client.post("/v1/listings", headers=as_user(seller), json={
        "card_id": str(card), "condition": "NM", "price_cents": price_cents, "quantity_available": quantity,
        **fields})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _address(client: AsyncClient, user: uuid.UUID, recipient_name: str | None = "Jo Buyer") -> str:
    resp = await client.post("/v1/addresses", headers=as_user(user), json={
        "recipient_name": recipient_name, "line1": "1 Main St", "city": "Springfield", "postal_code": "12345",
        "country": "US"})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _add(client: AsyncClient, buyer: uuid.UUID, listing: str, quantity: int = 1) -> Response:
    return await client.put(f"/v1/cart/items/{listing}", headers=as_user(buyer), json={"quantity": quantity})


async def _webhook(client: AsyncClient, event: dict[str, Any], signature: str = "valid") -> Response:
    return await client.post("/v1/stripe/webhook", content=json.dumps(event),
                             headers={"Stripe-Signature": signature, "Content-Type": "application/json"})


async def _get_listing(client: AsyncClient, listing: str) -> dict[str, Any]:
    return dict((await client.get(f"/v1/listings/{listing}")).json())


# ----------------------------------------------------------------
# Fees
# ----------------------------------------------------------------
def test_fee_terms() -> None:
    assert FeeTerms(Decimal("5"), 0, 0).fee_cents(1000, 100) == 50
    assert FeeTerms(Decimal("5"), 0, 0).fee_cents(1010, 0) == 51  # 50.5 rounds up
    assert FeeTerms(Decimal("8"), 15, 0).fee_cents(200, 100) == 31
    assert FeeTerms(Decimal("5"), 0, 30).fee_cents(100, 0) == 30
    # Never more than the order is worth.
    assert FeeTerms(Decimal("5"), 0, 500).fee_cents(100, 50) == 150


# ----------------------------------------------------------------
# Seller onboarding
# ----------------------------------------------------------------
async def test_seller_onboarding(client: AsyncClient, make_user, fake_stripe: FakeStripe) -> None:
    seller = await make_user(can_sell=False)
    status = (await client.get("/v1/sellers/me", headers=as_user(seller))).json()
    assert status == {"stripe_account_created": False, "can_sell": False}
    assert (await client.post("/v1/sellers/me/dashboard", headers=as_user(seller))).status_code == 409

    link = await client.post("/v1/sellers/me/onboarding", headers=as_user(seller))
    assert link.status_code == 200, link.text
    assert link.json()["url"].endswith("/sell/onboarding?done=1")
    status = (await client.get("/v1/sellers/me", headers=as_user(seller))).json()
    assert status == {"stripe_account_created": True, "can_sell": False}

    # Stripe finishes onboarding; the status call notices even without a webhook.
    account_id = f"acct_{str(seller)[:8]}"
    fake_stripe.accounts[account_id] = True
    assert (await client.get("/v1/sellers/me", headers=as_user(seller))).json()["can_sell"] is True
    assert (await client.post("/v1/sellers/me/dashboard", headers=as_user(seller))).status_code == 200

    # Stripe restricts the account later.
    fake_stripe.accounts[account_id] = False
    event = {"id": "evt_acct", "type": "account.updated",
             "data": {"object": {"id": account_id, "details_submitted": True,
                                 "capabilities": {"transfers": "inactive"}}}}
    assert (await _webhook(client, event)).status_code == 200
    assert (await client.get("/v1/sellers/me", headers=as_user(seller))).json()["can_sell"] is False


async def test_listings_from_unpayable_sellers_arent_for_sale(client: AsyncClient, make_user, make_card) -> None:
    seller, buyer = await make_user(can_sell=False), await make_user()
    card = await make_card()
    listing = await _listing(client, seller, card, 500)
    assert (await client.get(f"/v1/listings/card/{card}")).json()["items"] == []
    assert (await client.get(f"/v1/listings/card/{card}/summary")).json() == []
    resp = await _add(client, buyer, listing)
    assert (resp.status_code, resp.json()["error"]["message"]) == (409, "No longer for sale")


# ----------------------------------------------------------------
# Cart
# ----------------------------------------------------------------
async def test_cart(client: AsyncClient, make_user, make_card) -> None:
    alice, bob, buyer = await make_user("alice"), await make_user("bob"), await make_user()
    card = await make_card()
    a1 = await _listing(client, alice, card, 300, quantity=4)
    a2 = await _listing(client, alice, card, 200)
    b1 = await _listing(client, bob, card, 1000)
    await _rate(client, alice, 100)

    assert (await _add(client, alice, a1)).status_code == 400  # own listing
    assert (await _add(client, buyer, a1, 5)).status_code == 409
    assert (await _add(client, buyer, a1, 2)).status_code == 200
    assert (await _add(client, buyer, a1, 3)).status_code == 200  # sets, doesn't add
    await _add(client, buyer, a2)
    cart = (await _add(client, buyer, b1)).json()

    sellers = {s["seller"]["username"]: s for s in cart["sellers"]}
    assert sellers.keys() == {"alice", "bob"}
    assert {(i["listing"]["id"], i["quantity"]) for i in sellers["alice"]["items"]} == {(a1, 3), (a2, 1)}
    assert (sellers["alice"]["subtotal_cents"], cart["subtotal_cents"], cart["item_count"]) == (1100, 2100, 5)
    # Bob has no shipping rates yet.
    assert sellers["bob"]["shipping_rates"] == [] and cart["can_check_out"] is False
    await _rate(client, bob, 0, "Free")
    assert (await client.get("/v1/cart", headers=as_user(buyer))).json()["can_check_out"] is True

    # Alice sells some copies elsewhere: the cart flags it.
    await client.patch(f"/v1/listings/{a1}", headers=as_user(alice), json={"quantity_available": 2})
    cart = (await client.get("/v1/cart", headers=as_user(buyer))).json()
    problems = {i["listing"]["id"]: i["problem"] for s in cart["sellers"] for i in s["items"]}
    assert problems == {a1: "Only 2 available", a2: None, b1: None}
    assert cart["can_check_out"] is False

    cart = (await client.delete(f"/v1/cart/items/{a1}", headers=as_user(buyer))).json()
    assert cart["item_count"] == 2
    assert (await client.delete("/v1/cart", headers=as_user(buyer))).status_code == 204
    assert (await client.get("/v1/cart", headers=as_user(buyer))).json()["sellers"] == []


# ----------------------------------------------------------------
# Checkout
# ----------------------------------------------------------------
async def _two_seller_cart(client: AsyncClient, make_user, make_card) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    alice, bob, buyer = await make_user("alice"), await make_user("bob"), await make_user("buyer")
    card = await make_card()
    coll = (await client.post("/v1/collections", headers=as_user(alice), json={})).json()["id"]
    item = (await client.post(f"/v1/collections/{coll}/items", headers=as_user(alice),
                              json=[{"card_id": str(card), "quantity": 5, "condition": "NM"}])).json()[0]["id"]
    a1 = await _listing(client, alice, card, 300, quantity=3, collection_item_id=item)
    b1 = await _listing(client, bob, card, 1000)
    alice_cheap = await _rate(client, alice, 100)
    alice_tracked = await _rate(client, alice, 450, "Tracked")
    await _rate(client, bob, 0, "Free")
    await _add(client, buyer, a1, 2)
    await _add(client, buyer, b1)
    return {"alice": alice, "bob": bob, "buyer": buyer, "card": card, "item": item, "a1": a1, "b1": b1,
            "alice_cheap": alice_cheap, "alice_tracked": alice_tracked,
            "address": await _address(client, buyer)}


async def test_checkout_pay_ship_deliver(client: AsyncClient, make_user, make_card, fake_stripe: FakeStripe) -> None:
    s = await _two_seller_cart(client, make_user, make_card)
    buyer, alice, bob = s["buyer"], s["alice"], s["bob"]

    resp = await client.post("/v1/checkout", headers=as_user(buyer), json={
        "shipping_address_id": s["address"], "shipping_rates": {str(alice): s["alice_tracked"]}})
    assert resp.status_code == 201, resp.text
    checkout = resp.json()
    assert checkout["status"] == "open" and checkout["url"].startswith("https://checkout.stripe.test/")
    orders = {o["seller_id"]: o for o in checkout["orders"]}
    a_order, b_order = orders[str(alice)], orders[str(bob)]
    # Alice: 2 × $3 + $4.50 tracked; 5% of $6 = 30¢. Bob: $10, free shipping, 50¢.
    assert (a_order["subtotal_cents"], a_order["shipping_cents"], a_order["total_cents"]) == (600, 450, 1050)
    assert (a_order["platform_fee_cents"], a_order["seller_payout_cents"]) == (30, 1020)
    assert (b_order["total_cents"], b_order["platform_fee_cents"], b_order["seller_payout_cents"]) == (1000, 50, 950)
    assert checkout["total_cents"] == 2050
    assert a_order["shipping_address"]["recipient_name"] == "Jo Buyer"
    assert a_order["shipping_rate_name"] == "Tracked" and a_order["status"] == "pending"
    assert [(i["quantity"], i["price_cents"], i["condition"]) for i in a_order["items"]] == [(2, 300, "NM")]
    _, session = fake_stripe.session_for(checkout["id"])
    assert sorted(line.amount_cents for line in session.lines) == [1000, 1050]

    # The copies are held.
    a1 = await _get_listing(client, s["a1"])
    assert (a1["quantity_available"], a1["quantity_reserved"], a1["status"]) == (1, 2, "active")
    b1 = await _get_listing(client, s["b1"])
    assert (b1["quantity_available"], b1["quantity_reserved"], b1["status"]) == (0, 1, "pending_sale")
    assert (await client.delete(f"/v1/listings/{s['a1']}", headers=as_user(alice))).status_code == 409
    # 3 listed (1 available + 2 held) of 5: can't go below 3.
    assert (await client.patch(f"/v1/collections/items/{s['item']}", headers=as_user(alice),
                               json={"quantity": 2})).status_code == 409
    # Sellers don't see unpaid orders.
    assert (await client.get("/v1/orders", headers=as_user(alice), params={"role": "seller"})).json()["items"] == []

    # Stripe confirms payment.
    event = fake_stripe.completed_event(checkout["id"])
    assert (await _webhook(client, event, signature="forged")).status_code == 400
    assert (await _webhook(client, event)).status_code == 200
    assert (await _webhook(client, event)).status_code == 200  # redelivered: no-op
    checkout = (await client.get(f"/v1/checkout/{checkout['id']}", headers=as_user(buyer))).json()
    assert checkout["status"] == "paid" and checkout["url"] is None
    assert {o["status"] for o in checkout["orders"]} == {"paid"}

    a1 = await _get_listing(client, s["a1"])
    assert (a1["quantity_available"], a1["quantity_reserved"], a1["status"]) == (1, 0, "active")
    assert (await _get_listing(client, s["b1"]))["status"] == "sold"
    item = (await client.get(f"/v1/collections/items/{s['item']}", headers=as_user(alice))).json()
    assert (item["quantity"], item["quantity_listed"]) == (3, 1)
    assert (await client.get("/v1/cart", headers=as_user(buyer))).json()["sellers"] == []
    assert (await client.get(f"/v1/users/{alice}")).json()["total_sales"] == 1
    sold = (await client.get("/v1/orders", headers=as_user(alice), params={"role": "seller"})).json()["items"]
    assert [o["id"] for o in sold] == [a_order["id"]]
    assert fake_stripe.transfers == {}  # paid on shipping

    # Only the seller ships; shipping pays them from the buyer's charge.
    ship = f"/v1/orders/{a_order['id']}/ship"
    assert (await client.post(ship, headers=as_user(buyer), json={})).status_code == 403
    shipped = await client.post(ship, headers=as_user(alice), json={"tracking_number": "1Z999"})
    assert shipped.status_code == 200, shipped.text
    assert (shipped.json()["status"], shipped.json()["tracking_number"]) == ("shipped", "1Z999")
    transfer = fake_stripe.transfers[a_order["id"]]
    assert (transfer["amount"], transfer["destination"]) == (1020, f"acct_{str(alice)[:8]}")
    assert transfer["source_transaction"] == f"ch_{checkout['id'][:8]}"
    assert (await client.post(ship, headers=as_user(alice), json={})).status_code == 409

    assert (await client.post("/v1/ratings", headers=as_user(buyer),
                              json={"order_id": a_order["id"], "rating": 5})).status_code == 400
    received = await client.post(f"/v1/orders/{a_order['id']}/received", headers=as_user(buyer))
    assert received.json()["status"] == "delivered"
    assert (await client.post("/v1/ratings", headers=as_user(buyer),
                              json={"order_id": a_order["id"], "rating": 5})).status_code == 201


async def test_checkout_expiry_and_cancel(client: AsyncClient, make_user, make_card, fake_stripe: FakeStripe,
                                          db: AsyncSession) -> None:
    s = await _two_seller_cart(client, make_user, make_card)
    buyer = s["buyer"]
    body = {"shipping_address_id": s["address"]}
    first = (await client.post("/v1/checkout", headers=as_user(buyer), json=body)).json()

    # One checkout at a time.
    again = await client.post("/v1/checkout", headers=as_user(buyer), json=body)
    assert (again.status_code, again.json()["error"]["code"]) == (409, "checkout_in_progress")

    assert (await _webhook(client, fake_stripe.expired_event(first["id"]))).status_code == 200
    expired = (await client.get(f"/v1/checkout/{first['id']}", headers=as_user(buyer))).json()
    assert expired["status"] == "expired" and {o["status"] for o in expired["orders"]} == {"cancelled"}
    b1 = await _get_listing(client, s["b1"])
    assert (b1["quantity_available"], b1["quantity_reserved"], b1["status"]) == (1, 0, "active")
    # Payment for an expired checkout is refunded rather than accepted.
    assert (await _webhook(client, fake_stripe.completed_event(first["id"]))).status_code == 200
    assert fake_stripe.refunds == {f"checkout-{first['id']}": 1700}

    # The cart is untouched, so the buyer can try again, then cancel.
    second = (await client.post("/v1/checkout", headers=as_user(buyer), json=body)).json()
    cancelled = (await client.post(f"/v1/checkout/{second['id']}/cancel", headers=as_user(buyer))).json()
    assert cancelled["status"] == "expired"
    assert (await _get_listing(client, s["b1"]))["quantity_available"] == 1

    # A checkout past its expiry whose webhook never came is cleared on the next attempt.
    third = (await client.post("/v1/checkout", headers=as_user(buyer), json=body)).json()
    await db.execute(update(Checkout).where(Checkout.id == uuid.UUID(third["id"]))
                     .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
    fourth = await client.post("/v1/checkout", headers=as_user(buyer), json=body)
    assert fourth.status_code == 201, fourth.text
    old = (await client.get(f"/v1/checkout/{third['id']}", headers=as_user(buyer))).json()
    assert old["status"] == "expired"
    assert (await _get_listing(client, s["b1"]))["quantity_reserved"] == 1


async def test_checkout_refusals(client: AsyncClient, make_user, make_card) -> None:
    s = await _two_seller_cart(client, make_user, make_card)
    buyer, other = s["buyer"], await make_user()

    nameless = await _address(client, buyer, recipient_name=None)
    resp = await client.post("/v1/checkout", headers=as_user(buyer), json={"shipping_address_id": nameless})
    assert resp.status_code == 400 and "recipient" in resp.json()["error"]["message"]
    resp = await client.post("/v1/checkout", headers=as_user(buyer),
                             json={"shipping_address_id": await _address(client, other)})
    assert resp.status_code == 404
    resp = await client.post("/v1/checkout", headers=as_user(buyer), json={
        "shipping_address_id": s["address"], "shipping_rates": {str(s["bob"]): s["alice_cheap"]}})
    assert resp.status_code == 400

    # Another buyer takes Bob's only copy first.
    await _add(client, other, s["b1"])
    taken = await client.post("/v1/checkout", headers=as_user(other),
                              json={"shipping_address_id": await _address(client, other)})
    assert taken.status_code == 201, taken.text
    resp = await client.post("/v1/checkout", headers=as_user(buyer), json={"shipping_address_id": s["address"]})
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "cart_changed")
    assert "All copies are in other buyers' checkouts" in resp.json()["error"]["message"]

    # Under Stripe's 50¢ minimum.
    cheap_seller, small_buyer = await make_user(), await make_user()
    penny = await _listing(client, cheap_seller, s["card"], 10)
    await _rate(client, cheap_seller, 0)
    await _add(client, small_buyer, penny)
    resp = await client.post("/v1/checkout", headers=as_user(small_buyer),
                             json={"shipping_address_id": await _address(client, small_buyer)})
    assert resp.status_code == 400
    assert (await _get_listing(client, penny))["quantity_reserved"] == 0  # rolled back

    empty = await make_user()
    resp = await client.post("/v1/checkout", headers=as_user(empty),
                             json={"shipping_address_id": await _address(client, empty)})
    assert resp.status_code == 400


async def _paid_order(client: AsyncClient, fake_stripe: FakeStripe, s: dict[str, Any]) -> tuple[dict, dict]:
    checkout = (await client.post("/v1/checkout", headers=as_user(s["buyer"]),
                                  json={"shipping_address_id": s["address"]})).json()
    await _webhook(client, fake_stripe.completed_event(checkout["id"]))
    orders = {o["seller_id"]: o for o in checkout["orders"]}
    return orders[str(s["alice"])], orders[str(s["bob"])]


async def test_refund_unshipped_order(client: AsyncClient, make_user, make_card, fake_stripe: FakeStripe) -> None:
    s = await _two_seller_cart(client, make_user, make_card)
    a_order, _ = await _paid_order(client, fake_stripe, s)
    url = f"/v1/orders/{a_order['id']}/refund"
    assert (await client.post(url, headers=as_user(s["buyer"]))).status_code == 403
    refunded = await client.post(url, headers=as_user(s["alice"]))
    assert refunded.status_code == 200, refunded.text
    assert refunded.json()["status"] == "refunded"
    assert fake_stripe.refunds == {a_order["id"]: 700}
    assert fake_stripe.reversed == []
    assert (await client.post(f"/v1/orders/{a_order['id']}/ship", headers=as_user(s["alice"]),
                              json={})).status_code == 409


async def test_payout_when_paid(client: AsyncClient, make_user, make_card, fake_stripe: FakeStripe,
                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "payout_on", "paid")
    s = await _two_seller_cart(client, make_user, make_card)
    a_order, b_order = await _paid_order(client, fake_stripe, s)
    assert {k: v["amount"] for k, v in fake_stripe.transfers.items()} == {a_order["id"]: 670, b_order["id"]: 950}

    # Refunding takes the seller's transfer back first.
    await client.post(f"/v1/orders/{b_order['id']}/refund", headers=as_user(s["bob"]))
    assert fake_stripe.reversed == [fake_stripe.transfers[b_order["id"]]["id"]]


async def test_failed_payout_keeps_order_unshipped(client: AsyncClient, make_user, make_card,
                                                   fake_stripe: FakeStripe) -> None:
    s = await _two_seller_cart(client, make_user, make_card)
    a_order, _ = await _paid_order(client, fake_stripe, s)
    fake_stripe.fail_transfers = True
    resp = await client.post(f"/v1/orders/{a_order['id']}/ship", headers=as_user(s["alice"]), json={})
    assert (resp.status_code, resp.json()["error"]["code"]) == (502, "payment_provider_error")
    order = (await client.get(f"/v1/orders/{a_order['id']}", headers=as_user(s["alice"]))).json()
    assert order["status"] == "paid"


async def test_dispute_flags_orders(client: AsyncClient, make_user, make_card, fake_stripe: FakeStripe) -> None:
    s = await _two_seller_cart(client, make_user, make_card)
    a_order, _ = await _paid_order(client, fake_stripe, s)
    checkout_id = a_order["checkout_id"]
    event = {"id": "evt_dispute", "type": "charge.dispute.created",
             "data": {"object": {"id": "dp_1", "payment_intent": f"pi_{checkout_id[:8]}"}}}
    assert (await _webhook(client, event)).status_code == 200
    checkout = (await client.get(f"/v1/checkout/{checkout_id}", headers=as_user(s["buyer"]))).json()
    assert {o["status"] for o in checkout["orders"]} == {"disputed"}
