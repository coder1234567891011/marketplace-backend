import uuid
from typing import Any

from httpx import AsyncClient

from conftest import as_user
from services.wants.optimizer import Offer, optimize


# ----------------------------------------------------------------
# Optimizer (no database)
# ----------------------------------------------------------------
def _ids(*names: str) -> dict[str, uuid.UUID]:
    return {n: uuid.uuid4() for n in names}


def _named(plan_picks: dict[uuid.UUID, dict[uuid.UUID, int]], names: dict[str, uuid.UUID]) -> dict[str, dict[str, int]]:
    back = {v: k for k, v in names.items()}
    return {back[item]: {back[listing]: n for listing, n in picks.items()} for item, picks in plan_picks.items()}


def test_optimizer_saves_shipping() -> None:
    n = _ids("bolt", "helix", "A", "B", "C", "a_bolt", "b_bolt", "b_helix", "c_helix")
    offers = {
        n["bolt"]: [Offer(n["a_bolt"], n["A"], 100), Offer(n["b_bolt"], n["B"], 150)],
        n["helix"]: [Offer(n["c_helix"], n["C"], 100), Offer(n["b_helix"], n["B"], 150)],
    }
    stock = {n[k]: 4 for k in ("a_bolt", "b_bolt", "b_helix", "c_helix")}
    needs = {n["bolt"]: 1, n["helix"]: 1}
    # Each card is cheapest elsewhere, but one seller saves a shipping charge.
    plan = optimize(needs, offers, stock, {n["A"]: 400, n["B"]: 400, n["C"]: 400})
    assert _named(plan.picks, n) == {"bolt": {"b_bolt": 1}, "helix": {"b_helix": 1}} and plan.missing == {}
    # With free shipping, the cheapest copies win.
    plan = optimize(needs, offers, stock, {n["A"]: 0, n["B"]: 0, n["C"]: 0})
    assert _named(plan.picks, n) == {"bolt": {"a_bolt": 1}, "helix": {"c_helix": 1}}


def test_optimizer_stock_and_missing() -> None:
    n = _ids("any", "exact", "A", "B", "a1", "b1")
    # Both items can use listing a1, which has 3 copies.
    offers = {n["any"]: [Offer(n["a1"], n["A"], 100), Offer(n["b1"], n["B"], 300)],
              n["exact"]: [Offer(n["a1"], n["A"], 100)]}
    plan = optimize({n["any"]: 2, n["exact"]: 2}, offers, {n["a1"]: 3, n["b1"]: 1}, {n["A"]: 100, n["B"]: 100})
    # The scarce item is filled first; the other gets what's left and one copy elsewhere.
    assert _named(plan.picks, n) == {"exact": {"a1": 2}, "any": {"a1": 1, "b1": 1}} and plan.missing == {}
    plan = optimize({n["any"]: 5}, offers, {n["a1"]: 3, n["b1"]: 1}, {n["A"]: 100})  # B has no shipping rate
    assert _named(plan.picks, n) == {"any": {"a1": 3}} and plan.missing == {n["any"]: 2}


# ----------------------------------------------------------------
# API
# ----------------------------------------------------------------
async def _listing(client: AsyncClient, seller: uuid.UUID, card: uuid.UUID, price_cents: int,
                   condition: str = "NM", quantity: int = 4, **fields: Any) -> str:
    resp = await client.post("/v1/listings", headers=as_user(seller), json={
        "card_id": str(card), "condition": condition, "price_cents": price_cents, "quantity_available": quantity,
        **fields})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _rate(client: AsyncClient, seller: uuid.UUID, price_cents: int) -> None:
    resp = await client.post("/v1/shipping-rates", headers=as_user(seller),
                             json={"name": "Envelope", "price_cents": price_cents})
    assert resp.status_code == 201, resp.text


async def _list(client: AsyncClient, user: uuid.UUID, name: str = "Burn") -> str:
    resp = await client.post("/v1/wants", headers=as_user(user), json={"name": name})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


async def _items(client: AsyncClient, user: uuid.UUID, wants: str) -> dict[str, dict[str, Any]]:
    resp = await client.get(f"/v1/wants/{wants}/items", headers=as_user(user))
    assert resp.status_code == 200, resp.text
    return {f"{i['card']['name']}{'' if i['any_printing'] else ' (exact)'}": i for i in resp.json()}


async def test_lists_and_import(client: AsyncClient, make_user, make_card) -> None:
    user, other = await make_user(), await make_user()
    await make_card("Lightning Bolt", set_code="m10", collector_number="146")
    await make_card("Lightning Helix", set_code="rav", collector_number="213")
    wants = await _list(client, user)

    deck = "4 Lightning Bolt\n1 Lightning Helix (RAV) 213\n2 Nonexistent Card\n"
    resp = await client.post(f"/v1/wants/{wants}/import", headers=as_user(user), json={"text": deck})
    assert resp.status_code == 200, resp.text
    assert (resp.json()["added"], resp.json()["updated"]) == (2, 0)
    assert [p["line"] for p in resp.json()["unmatched"]] == [3]
    resp = await client.post(f"/v1/wants/{wants}/import", headers=as_user(user), json={"text": "2 Lightning Bolt"})
    assert (resp.json()["added"], resp.json()["updated"]) == (0, 1)

    items = await _items(client, user, wants)
    assert items.keys() == {"Lightning Bolt", "Lightning Helix (exact)"}
    assert items["Lightning Bolt"]["quantity"] == 6
    lists = (await client.get("/v1/wants", headers=as_user(user))).json()
    assert [(wl["name"], wl["item_count"]) for wl in lists] == [("Burn", 2)]

    item = items["Lightning Bolt"]["id"]
    resp = await client.patch(f"/v1/wants/items/{item}", headers=as_user(user),
                              json={"quantity": 4, "min_condition": "LP", "max_price_cents": 250})
    assert (resp.json()["quantity"], resp.json()["min_condition"]) == (4, "LP")
    resp = await client.patch(f"/v1/wants/items/{item}", headers=as_user(user), json={"min_condition": None})
    assert resp.json()["min_condition"] is None and resp.json()["max_price_cents"] == 250

    # Other people can't see or change it.
    assert (await client.get(f"/v1/wants/{wants}/items", headers=as_user(other))).status_code == 404
    assert (await client.delete(f"/v1/wants/items/{item}", headers=as_user(other))).status_code == 404
    assert (await client.delete(f"/v1/wants/items/{item}", headers=as_user(user))).status_code == 204
    assert (await client.delete(f"/v1/wants/{wants}", headers=as_user(user))).status_code == 204
    assert (await client.get("/v1/wants", headers=as_user(user))).json() == []


async def test_owned_available_and_alerts(client: AsyncClient, make_user, make_card) -> None:
    user, seller = await make_user(), await make_user()
    oracle = uuid.uuid4()  # two printings of the same card
    m10 = await make_card("Lightning Bolt", set_code="m10", collector_number="146", oracle_id=oracle)
    m21 = await make_card("Lightning Bolt", set_code="m21", collector_number="150", oracle_id=oracle)
    wants = await _list(client, user)
    for body in ({"card_id": str(m10), "quantity": 4, "min_condition": "LP", "max_price_cents": 250},
                 {"card_id": str(m21), "any_printing": False, "finish": "foil"}):
        resp = await client.post(f"/v1/wants/{wants}/items", headers=as_user(user), json=body)
        assert resp.status_code == 201, resp.text

    lp = await _listing(client, seller, m10, 200, condition="LP", quantity=1)
    nm = await _listing(client, seller, m10, 300, quantity=2)
    await _listing(client, seller, m21, 100, condition="MP")          # worse than LP
    foil = await _listing(client, seller, m21, 500, quantity=1, finish="foil")
    await _listing(client, user, m10, 50)                              # the buyer's own
    await _rate(client, seller, 100)
    binder = (await client.post("/v1/collections", headers=as_user(user), json={"name": "Binder"})).json()["id"]
    await client.post(f"/v1/collections/{binder}/items", headers=as_user(user), json=[{"card_id": str(m21)}])

    items = await _items(client, user, wants)
    bolt, exact = items["Lightning Bolt"], items["Lightning Bolt (exact)"]
    # Owning the m21 printing counts for "any printing", not for the exact foil. Any printing
    # includes the foil listing too; the MP copy and the buyer's own aren't counted.
    assert (bolt["owned"], bolt["available"], bolt["lowest_price_cents"]) == (1, 4, 200)
    assert (exact["owned"], exact["available"], exact["lowest_price_cents"]) == (0, 1, 500)

    alerts = (await client.get(f"/v1/wants/{wants}/alerts", headers=as_user(user))).json()
    assert [a["id"] for a in alerts] == [lp]

    resp = await client.post(f"/v1/wants/{wants}/optimize", headers=as_user(user), json={"add_to_cart": True})
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    [planned] = plan["sellers"]
    assert {ln["listing"]["id"]: ln["quantity"] for ln in planned["lines"]} == {lp: 1, nm: 2, foil: 1}
    assert (plan["subtotal_cents"], plan["shipping_cents"], plan["total_cents"]) == (1300, 100, 1400)
    assert plan["missing"] == [] and plan["added_to_cart"] is True
    cart = (await client.get("/v1/cart", headers=as_user(user))).json()
    assert {i["listing"]["id"]: i["quantity"] for s in cart["sellers"] for i in s["items"]} == {lp: 1, nm: 2, foil: 1}

    # Counting owned copies too, one Bolt can't be found.
    plan = (await client.post(f"/v1/wants/{wants}/optimize", headers=as_user(user),
                              json={"skip_owned": False})).json()
    assert [(m["want_item_id"], m["quantity"]) for m in plan["missing"]] == [(bolt["id"], 1)]
