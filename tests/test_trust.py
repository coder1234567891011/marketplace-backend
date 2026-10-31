import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from conftest import as_user
from fake_stripe import FakeStripe
from models.database import Email, EmailStatus, Order, OrderIssue, User, auth_users
from services.email.sender import send_pending
from services.orders.maintenance import run_maintenance
from test_checkout import _paid_order, _two_seller_cart
from utils.settings import settings


async def _emails(db: AsyncSession, user: uuid.UUID) -> list[str]:
    """Templates queued for user, sorted: in a test everything shares one
    transaction, so created_at (now()) can't order them."""
    return sorted((await db.scalars(select(Email.template).where(Email.user_id == user))).all())


async def _ship(client: AsyncClient, seller: uuid.UUID, order: dict[str, Any]) -> None:
    resp = await client.post(f"/v1/orders/{order['id']}/ship", headers=as_user(seller), json={})
    assert resp.status_code == 200, resp.text


async def _open(client: AsyncClient, buyer: uuid.UUID, order: dict[str, Any],
                reason: str = "not_as_described") -> dict[str, Any]:
    resp = await client.post(f"/v1/orders/{order['id']}/issues", headers=as_user(buyer),
                             json={"reason": reason, "message": "Arrived as MP, listed as NM"})
    assert resp.status_code == 201, resp.text
    return dict(resp.json())


async def _make_admin(db: AsyncSession, user: uuid.UUID) -> None:
    await db.execute(update(User).where(User.id == user).values(is_admin=True))
    await db.flush()


# ----------------------------------------------------------------
# Issues between buyer and seller
# ----------------------------------------------------------------
async def test_issue_partial_then_full_refund(client: AsyncClient, make_user, make_card, fake_stripe: FakeStripe,
                                              db: AsyncSession) -> None:
    s = await _two_seller_cart(client, make_user, make_card)
    buyer, alice = s["buyer"], s["alice"]
    a_order, _ = await _paid_order(client, fake_stripe, s)   # $7: 2 × $3 + $1 shipping; Alice gets $6.70
    await _ship(client, alice, a_order)
    stranger = await make_user()

    url = f"/v1/orders/{a_order['id']}/issues"
    assert (await client.post(url, headers=as_user(alice),
                              json={"reason": "other", "message": "x"})).status_code == 403
    assert (await client.post(url, headers=as_user(stranger),
                              json={"reason": "other", "message": "x"})).status_code == 404
    issue = await _open(client, buyer, a_order)
    assert (issue["status"], issue["reason"], issue["seller_id"]) == ("open", "not_as_described", str(alice))
    assert [m["body"] for m in issue["messages"]] == ["Arrived as MP, listed as NM"]
    again = await client.post(url, headers=as_user(buyer), json={"reason": "other", "message": "again"})
    assert (again.status_code, again.json()["error"]["code"]) == (409, "issue_open")
    assert await _emails(db, alice) == ["issue_opened", "order_sold"]

    # Only the parties see it; the seller gets 3 days before the buyer can escalate.
    issue_url = f"/v1/issues/{issue['id']}"
    assert (await client.get(issue_url, headers=as_user(stranger))).status_code == 404
    early = await client.post(f"{issue_url}/escalate", headers=as_user(buyer))
    assert (early.status_code, early.json()["error"]["code"]) == (409, "too_early")
    reply = await client.post(f"{issue_url}/messages", headers=as_user(alice), json={"body": "Sorry! Partial refund?"})
    assert {m["from_moderator"] for m in reply.json()["messages"]} == {False}
    assert "issue_message" in await _emails(db, buyer)
    selling = (await client.get("/v1/issues", headers=as_user(alice), params={"role": "seller"})).json()
    assert [i["id"] for i in selling["items"]] == [issue["id"]]

    # A partial refund comes out of Alice's payout, which was sent on shipping.
    assert (await client.post(f"{issue_url}/refund", headers=as_user(buyer), json={})).status_code == 403
    assert (await client.post(f"{issue_url}/refund", headers=as_user(alice),
                              json={"amount_cents": 701})).status_code == 400
    refunded = await client.post(f"{issue_url}/refund", headers=as_user(alice), json={"amount_cents": 300})
    assert refunded.status_code == 200, refunded.text
    assert (refunded.json()["status"], refunded.json()["resolution"], refunded.json()["refund_cents"]) == (
        "resolved", "refunded", 300)
    transfer = fake_stripe.transfers[a_order["id"]]["id"]
    assert fake_stripe.refunds == {f"issue-{issue['id']}": 300}
    assert fake_stripe.reversed == [(transfer, 300)]
    order = (await client.get(f"/v1/orders/{a_order['id']}", headers=as_user(buyer))).json()
    assert (order["status"], order["refunded_cents"]) == ("shipped", 300)
    assert (await client.post(f"{issue_url}/messages", headers=as_user(buyer),
                              json={"body": "thanks"})).status_code == 409

    # A second issue, refunded in full: the rest of the payout (370) is clawed back
    # and the platform gives up its 30¢ fee.
    second = await _open(client, buyer, a_order, reason="other")
    full = await client.post(f"/v1/issues/{second['id']}/refund", headers=as_user(alice), json={})
    assert full.json()["refund_cents"] == 400
    assert fake_stripe.reversed[-1] == (transfer, 370)
    order = (await client.get(f"/v1/orders/{a_order['id']}", headers=as_user(buyer))).json()
    assert (order["status"], order["refunded_cents"]) == ("refunded", 700)
    assert (await client.post(url, headers=as_user(buyer),
                              json={"reason": "other", "message": "x"})).status_code == 409
    assert await _emails(db, buyer) == sorted(["order_placed", "order_shipped", "issue_message", "issue_resolved",
                                               "order_refunded", "issue_resolved", "order_refunded"])


async def test_seller_cancel_settles_issue(client: AsyncClient, make_user, make_card, fake_stripe: FakeStripe) -> None:
    s = await _two_seller_cart(client, make_user, make_card)
    a_order, _ = await _paid_order(client, fake_stripe, s)
    issue = await _open(client, s["buyer"], a_order, reason="not_received")
    assert (await client.post(f"/v1/orders/{a_order['id']}/refund", headers=as_user(s["alice"]))).status_code == 200
    closed = (await client.get(f"/v1/issues/{issue['id']}", headers=as_user(s["buyer"]))).json()
    assert (closed["resolution"], closed["refund_cents"]) == ("refunded", 700)
    assert fake_stripe.refunds == {a_order["id"]: 700}


async def test_payout_on_delivery_waits_for_issues(client: AsyncClient, make_user, make_card, fake_stripe: FakeStripe,
                                                   db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "payout_on", "delivered")
    s = await _two_seller_cart(client, make_user, make_card)
    buyer, alice, bob = s["buyer"], s["alice"], s["bob"]
    a_order, b_order = await _paid_order(client, fake_stripe, s)
    await _ship(client, alice, a_order)
    await _ship(client, bob, b_order)
    assert fake_stripe.transfers == {}

    # Receiving Alice's order closes the "not received" issue and pays her.
    issue = await _open(client, buyer, a_order, reason="not_received")
    await client.post(f"/v1/orders/{a_order['id']}/received", headers=as_user(buyer))
    closed = (await client.get(f"/v1/issues/{issue['id']}", headers=as_user(buyer))).json()
    assert (closed["status"], closed["resolution"]) == ("resolved", "withdrawn")
    assert fake_stripe.transfers[a_order["id"]]["amount"] == 670

    # Bob's: a refund before delivery comes out of the payout he hasn't had yet.
    issue = await _open(client, buyer, b_order)
    await client.post(f"/v1/issues/{issue['id']}/refund", headers=as_user(bob), json={"amount_cents": 200})
    assert fake_stripe.reversed == []

    # Never marked received: auto-delivered after 21 days, unless an issue is open.
    old = datetime.now(timezone.utc) - timedelta(days=22)
    await db.execute(update(Order).where(Order.id == uuid.UUID(b_order["id"])).values(shipped_at=old))
    pending = await _open(client, buyer, b_order, reason="other")
    assert (await run_maintenance(db, datetime.now(timezone.utc))).delivered == 0
    await client.post(f"/v1/issues/{pending['id']}/withdraw", headers=as_user(buyer))
    result = await run_maintenance(db, datetime.now(timezone.utc))
    assert (result.delivered, result.paid_out, result.payouts_failed) == (1, 1, 0)
    assert fake_stripe.transfers[b_order["id"]]["amount"] == 950 - 200
    assert (await run_maintenance(db, datetime.now(timezone.utc))).paid_out == 0


async def test_issue_window(client: AsyncClient, make_user, make_card, fake_stripe: FakeStripe,
                            db: AsyncSession) -> None:
    s = await _two_seller_cart(client, make_user, make_card)
    a_order, _ = await _paid_order(client, fake_stripe, s)
    await _ship(client, s["alice"], a_order)
    await client.post(f"/v1/orders/{a_order['id']}/received", headers=as_user(s["buyer"]))
    await db.execute(update(Order).where(Order.id == uuid.UUID(a_order["id"]))
                     .values(delivered_at=datetime.now(timezone.utc) - timedelta(days=31)))
    late = await client.post(f"/v1/orders/{a_order['id']}/issues", headers=as_user(s["buyer"]),
                             json={"reason": "other", "message": "late"})
    assert late.status_code == 409


# ----------------------------------------------------------------
# Moderators
# ----------------------------------------------------------------
async def test_escalated_issue_decided_by_moderator(client: AsyncClient, make_user, make_card,
                                                    fake_stripe: FakeStripe, db: AsyncSession) -> None:
    s = await _two_seller_cart(client, make_user, make_card)
    buyer, alice = s["buyer"], s["alice"]
    a_order, _ = await _paid_order(client, fake_stripe, s)
    await _ship(client, alice, a_order)
    issue = await _open(client, buyer, a_order)
    admin = await make_user()

    assert (await client.get("/v1/admin/issues", headers=as_user(admin))).json()["error"]["code"] == "not_moderator"
    await _make_admin(db, admin)

    await db.execute(update(OrderIssue).where(OrderIssue.id == uuid.UUID(issue["id"]))
                     .values(created_at=datetime.now(timezone.utc) - timedelta(days=4)))
    escalated = await client.post(f"/v1/issues/{issue['id']}/escalate", headers=as_user(buyer))
    assert escalated.json()["status"] == "escalated"
    queue = (await client.get("/v1/admin/issues", headers=as_user(admin))).json()["items"]
    assert [i["id"] for i in queue] == [issue["id"]]

    asked = await client.post(f"/v1/admin/issues/{issue['id']}/messages", headers=as_user(admin),
                              json={"body": "Photos of the card, please"})
    assert sorted(m["from_moderator"] for m in asked.json()["messages"]) == [False, True]
    decided = await client.post(f"/v1/admin/issues/{issue['id']}/resolve", headers=as_user(admin),
                                json={"refund_cents": 0, "note": "The photos show NM"})
    assert decided.status_code == 200, decided.text
    assert (decided.json()["status"], decided.json()["resolution"]) == ("resolved", "no_refund")
    assert fake_stripe.refunds == {}
    log = (await client.get("/v1/admin/actions", headers=as_user(admin),
                            params={"target_id": issue["id"]})).json()["items"]
    assert [(a["action"], a["details"]["refund_cents"]) for a in log] == [("resolve_issue", 0)]
    assert (await client.post(f"/v1/admin/issues/{issue['id']}/resolve", headers=as_user(admin),
                              json={"refund_cents": 0})).status_code == 409


async def test_reports_and_moderation(client: AsyncClient, make_user, make_card, make_order, fake_stripe: FakeStripe,
                                      db: AsyncSession) -> None:
    s = await _two_seller_cart(client, make_user, make_card)
    buyer, bob, admin = s["buyer"], s["bob"], await make_user()
    await _make_admin(db, admin)

    body = {"target_type": "listing", "target_id": s["b1"], "reason": "counterfeit", "details": "Fake foil"}
    report = await client.post("/v1/reports", headers=as_user(buyer), json=body)
    assert report.status_code == 201, report.text
    dup = await client.post("/v1/reports", headers=as_user(buyer), json=body)
    assert (dup.status_code, dup.json()["error"]["code"]) == (409, "already_reported")
    assert (await client.post("/v1/reports", headers=as_user(bob), json=body)).status_code == 400
    missing = body | {"target_id": str(uuid.uuid4())}
    assert (await client.post("/v1/reports", headers=as_user(buyer), json=missing)).status_code == 404
    assert (await client.get("/v1/admin/reports", headers=as_user(buyer))).status_code == 403
    queue = (await client.get("/v1/admin/reports", headers=as_user(admin))).json()["items"]
    assert [(r["reporter_id"], r["reason"]) for r in queue] == [(str(buyer), "counterfeit")]

    # Taking the listing down closes its reports.
    resp = await client.post(f"/v1/admin/listings/{s['b1']}/remove", headers=as_user(admin), json={"note": "Fake"})
    assert resp.status_code == 204
    assert (await client.get(f"/v1/listings/{s['b1']}")).json()["status"] == "removed"
    mine = (await client.get("/v1/reports", headers=as_user(buyer))).json()["items"]
    assert [r["status"] for r in mine] == ["actioned"]
    assert (await client.get("/v1/admin/reports", headers=as_user(admin))).json()["items"] == []

    # Suspending Bob: he can read but not write, and is emailed.
    assert (await client.put(f"/v1/admin/users/{admin}/status", headers=as_user(admin),
                             json={"account_status": "banned"})).status_code == 400
    resp = await client.put(f"/v1/admin/users/{bob}/status", headers=as_user(admin),
                            json={"account_status": "suspended", "note": "Counterfeits"})
    assert resp.json()["account_status"] == "suspended"
    assert (await client.get("/v1/users/me", headers=as_user(bob))).status_code == 200
    blocked = await client.post("/v1/shipping-rates", headers=as_user(bob), json={"name": "x", "price_cents": 1})
    assert blocked.json()["error"]["code"] == "account_suspended"
    assert "account_status" in await _emails(db, bob)

    # Removing an abusive rating recomputes the average.
    delivered = await make_order(buyer, bob, "delivered")
    rating = (await client.post("/v1/ratings", headers=as_user(buyer),
                                json={"order_id": str(delivered), "rating": 1, "comment": "rude"})).json()
    assert (await client.get(f"/v1/users/{bob}")).json()["seller_rating_count"] == 1
    await client.post("/v1/reports", headers=as_user(admin),
                      json={"target_type": "rating", "target_id": rating["id"], "reason": "harassment"})
    assert (await client.post(f"/v1/admin/ratings/{rating['id']}/remove", headers=as_user(admin),
                              json={})).status_code == 204
    profile = (await client.get(f"/v1/users/{bob}")).json()
    assert (profile["seller_rating_count"], profile["seller_rating_avg"]) == (0, None)
    actions = (await client.get("/v1/admin/actions", headers=as_user(admin))).json()["items"]
    assert {a["action"] for a in actions} == {"remove_rating", "set_account_status", "remove_listing"}


# ----------------------------------------------------------------
# Emails
# ----------------------------------------------------------------
async def test_email_sending(db: AsyncSession, make_user, monkeypatch: pytest.MonkeyPatch) -> None:
    user, nobody = await make_user(), await make_user()
    await db.execute(update(auth_users).where(auth_users.c.id == user).values(email="jo@example.com"))
    for template, data in (("order_shipped", {"order_id": "o1", "order": "O1", "total_cents": 500,
                                              "currency": "usd", "tracking_number": "1Z9"}),
                           ("account_status", {"account_status": "active", "note": ""})):
        db.add(Email(user_id=user, template=template, data=data))
    db.add(Email(user_id=nobody, template="account_status", data={"account_status": "active"}))
    await db.flush()

    # Not configured: everything is skipped rather than piling up.
    monkeypatch.setattr(settings, "resend_api_key", "")
    assert (await send_pending(db)).skipped == 3

    monkeypatch.setattr(settings, "resend_api_key", "re_test")
    monkeypatch.setattr(settings, "email_from", "Market <orders@example.com>")
    monkeypatch.setattr(settings, "email_max_attempts", 2)
    sent: list[dict[str, Any]] = []
    fail = {"now": True}

    def handler(request: httpx.Request) -> httpx.Response:
        if fail["now"]:
            return httpx.Response(500, text="provider down")
        sent.append(json.loads(request.content) | {"key": request.headers["Idempotency-Key"]})
        return httpx.Response(200, json={"id": "em_1"})

    email = Email(user_id=user, template="order_shipped", data={"order_id": "o2", "order": "O2", "total_cents": 1,
                                                                "currency": "usd", "tracking_number": ""})
    db.add(email)
    await db.flush()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert (await send_pending(db, http)).failed == 1
        assert (email.status, email.attempts, email.last_error) == (EmailStatus.pending, 1, "provider down")
        fail["now"] = False
        assert (await send_pending(db, http)).sent == 1
    assert email.status == EmailStatus.sent and email.sent_at is not None
    [message] = sent
    assert (message["to"], message["subject"], message["key"]) == (["jo@example.com"], "Order O2 has shipped",
                                                                  str(email.id))
    assert "Mark it received" in message["text"]

    gives_up = Email(user_id=user, template="account_status", data={"account_status": "banned"})
    db.add(gives_up)
    await db.flush()
    fail["now"] = True
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await send_pending(db, http)
        await send_pending(db, http)
    assert (gives_up.status, gives_up.attempts) == (EmailStatus.failed, 2)


async def test_emails_sent_after_commit(client: AsyncClient, make_user, db: AsyncSession,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from services.email import outbox

    calls: list[str] = []

    async def fake_send() -> None:
        calls.append("sent")

    monkeypatch.setattr(outbox, "_send_now", fake_send)
    monkeypatch.setattr(settings, "resend_api_key", "re_test")
    monkeypatch.setattr(settings, "email_from", "orders@example.com")
    bob, admin = await make_user(), await make_user()
    await _make_admin(db, admin)

    # A request that fails sends nothing.
    resp = await client.put(f"/v1/admin/users/{admin}/status", headers=as_user(admin),
                            json={"account_status": "banned"})
    assert resp.status_code == 400
    await asyncio.sleep(0)
    assert calls == []

    await client.put(f"/v1/admin/users/{bob}/status", headers=as_user(admin), json={"account_status": "suspended"})
    await asyncio.sleep(0)
    assert calls == ["sent"]
