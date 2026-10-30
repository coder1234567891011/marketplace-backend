"""
Stripe webhook events. Each event is recorded in stripe_events in the same
transaction that handles it, so a redelivered event is skipped, and a
failure rolls back both and Stripe retries later.

Events to enable on the webhook endpoint (Stripe dashboard → Developers →
Webhooks), for "Your account" and for "Connected accounts":
    checkout.session.completed, checkout.session.async_payment_succeeded,
    checkout.session.expired, charge.dispute.created, account.updated
"""
import logging
import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import StripeEvent
from services.checkout.checkout import mark_disputed, mark_paid, release
from services.payments.stripe_gateway import account_is_ready
from services.sellers.sellers import account_updated

log = logging.getLogger(__name__)


def _checkout_id(session_object: dict[str, Any]) -> uuid.UUID | None:
    raw = (session_object.get("metadata") or {}).get("checkout_id") or session_object.get("client_reference_id")
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


async def handle_event(session: AsyncSession, event: dict[str, Any]) -> None:
    first_time = await session.scalar(
        insert(StripeEvent).values(id=event["id"], type=event["type"])
        .on_conflict_do_nothing().returning(StripeEvent.id))
    if first_time is None:
        return
    kind, obj = event["type"], event["data"]["object"]

    if kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        checkout_id = _checkout_id(obj)
        # A completed session can still be waiting on a delayed payment method.
        if checkout_id is not None and obj.get("payment_status") == "paid":
            await mark_paid(session, checkout_id, obj["payment_intent"])
    elif kind == "checkout.session.expired":
        checkout_id = _checkout_id(obj)
        if checkout_id is not None:
            await release(session, checkout_id)
    elif kind == "charge.dispute.created":
        if obj.get("payment_intent"):
            await mark_disputed(session, obj["payment_intent"])
    elif kind == "account.updated":
        await account_updated(session, obj["id"], account_is_ready(obj))
    else:
        log.info("Ignoring Stripe event %s", kind)
