"""
Orders: one per seller in a checkout. They're created by checkout
(services/checkout), never directly from client input; afterwards the
seller ships or refunds them and the buyer confirms delivery. Problems
after payment go through order issues (services/issues).
"""
import logging
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth.auth import CurrentUser
from models.database import (
    Checkout,
    IssueReason,
    IssueResolution,
    IssueStatus,
    Order,
    OrderIssue,
    OrderItem,
    OrderStatus,
    User,
)
from models.object_models.orders import OrderRole
from services.email.outbox import queue_email
from services.payments.stripe_gateway import StripeFailed, get_gateway
from utils.errors import BadRequest, Conflict, Forbidden, NotFound
from utils.pagination import paginate
from utils.settings import settings

log = logging.getLogger(__name__)

# Allowed status changes. pending → cancelled is an unpaid checkout expiring;
# refunded means refunded in full (partial refunds keep the status);
# disputed is set by Stripe's dispute webhook and resolved outside the API for now.
TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.pending: {OrderStatus.paid, OrderStatus.cancelled},
    OrderStatus.paid: {OrderStatus.shipped, OrderStatus.refunded, OrderStatus.disputed},
    OrderStatus.shipped: {OrderStatus.delivered, OrderStatus.refunded, OrderStatus.disputed},
    OrderStatus.delivered: {OrderStatus.refunded, OrderStatus.disputed},
    OrderStatus.disputed: set(),
    OrderStatus.refunded: set(),
    OrderStatus.cancelled: set(),
}


def change_order_status(order: Order, new_status: OrderStatus) -> None:
    if new_status not in TRANSITIONS[order.status]:
        raise Conflict(f"A {order.status.value} order can't become {new_status.value}")
    order.status = new_status


def with_items():  # type: ignore[no-untyped-def]
    return selectinload(Order.items).selectinload(OrderItem.card)


async def get_my_orders(
    session: AsyncSession, current: CurrentUser, role: OrderRole, limit: int, cursor: str | None
) -> tuple[Sequence[Order], str | None]:
    if role == OrderRole.buyer:
        stmt = select(Order).where(Order.buyer_id == current.id)
    else:
        # Sellers only see orders once they're paid.
        stmt = select(Order).where(Order.seller_id == current.id,
                                   Order.status.not_in((OrderStatus.pending, OrderStatus.cancelled)))
    return await paginate(session, stmt.options(with_items()), Order, limit, cursor)


async def _load_order(session: AsyncSession, order_id: uuid.UUID, lock: bool = False) -> Order | None:
    stmt = select(Order).where(Order.id == order_id).options(with_items())
    if lock:
        stmt = stmt.with_for_update(of=Order)
    return await session.scalar(stmt)


async def get_order(session: AsyncSession, current: CurrentUser, order_id: uuid.UUID) -> Order:
    order = await _load_order(session, order_id)
    # Only the buyer and seller can see an order.
    if order is None or current.id not in (order.buyer_id, order.seller_id):
        raise NotFound("Order not found")
    return order


async def _get_party_order(session: AsyncSession, current: CurrentUser, order_id: uuid.UUID,
                           seller: bool) -> Order:
    order = await _load_order(session, order_id, lock=True)
    if order is None or current.id not in (order.buyer_id, order.seller_id):
        raise NotFound("Order not found")
    if (order.seller_id if seller else order.buyer_id) != current.id:
        raise Forbidden(f"Only the {'seller' if seller else 'buyer'} can do this")
    return order


def short_id(order: Order) -> str:
    """How orders are referred to in emails."""
    return str(order.id)[:8].upper()


def order_email(order: Order) -> dict[str, str | int]:
    return {"order_id": str(order.id), "order": short_id(order), "total_cents": order.total_cents,
            "currency": order.currency}


async def pay_out(session: AsyncSession, order: Order) -> None:
    """Transfer the seller's share of a paid order, less anything refunded
    from it, to their Stripe account. Safe to retry: the transfer's
    idempotency key is the order id."""
    amount = order.seller_payout_cents - order.seller_refunded_cents
    if order.stripe_transfer_id is not None or amount <= 0:
        return
    checkout = await session.get(Checkout, order.checkout_id)
    seller = await session.get(User, order.seller_id)
    if checkout is None or checkout.stripe_charge_id is None or seller is None:
        raise Conflict("This order wasn't paid through checkout")
    if seller.stripe_connect_account_id is None:
        raise Conflict("Set up payouts before shipping (POST /v1/sellers/me/onboarding)")
    order.stripe_transfer_id = await get_gateway().transfer(
        str(order.id), amount, order.currency, seller.stripe_connect_account_id,
        checkout.stripe_charge_id, transfer_group=str(checkout.id))
    order.paid_out_at = datetime.now(timezone.utc)


def close_issue(issue: OrderIssue, resolution: IssueResolution, resolved_by: uuid.UUID) -> None:
    issue.status, issue.resolution, issue.resolved_by = IssueStatus.resolved, resolution, resolved_by
    issue.resolved_at = datetime.now(timezone.utc)


async def has_unresolved_issue(session: AsyncSession, order_id: uuid.UUID) -> bool:
    return await session.scalar(select(OrderIssue.id).where(
        OrderIssue.order_id == order_id, OrderIssue.status != IssueStatus.resolved)) is not None


async def pay_out_if_settled(session: AsyncSession, order: Order) -> None:
    """With payout_on=delivered: pay the seller once the order is delivered
    and no issue is open. A failed transfer is left for the nightly
    maintenance run to retry, so it never blocks the buyer's request."""
    if settings.payout_on != "delivered" or order.status != OrderStatus.delivered:
        return
    if await has_unresolved_issue(session, order.id):
        return
    try:
        await pay_out(session, order)
    except (StripeFailed, Conflict) as e:
        log.warning("Payout for order %s failed, will retry: %s", order.id, e)


async def refund(session: AsyncSession, order: Order, amount_cents: int, key: str) -> None:
    """Refund part or all of what's left of an order to the buyer.

    The refund comes out of the seller's payout first (clawed back from
    their Stripe account if they were already paid); only what's beyond the
    payout, i.e. the platform's fee, is covered by the platform. A full
    refund makes the order refunded. key makes the Stripe calls idempotent,
    so retrying the same refund can't pay out twice."""
    if order.status == OrderStatus.disputed:
        raise Conflict("The buyer disputed this payment with their bank; Stripe handles the refund")
    remaining = order.total_cents - order.refunded_cents
    if not 0 < amount_cents <= remaining:
        raise BadRequest(f"The refund must be between 1 and {remaining} cents")
    checkout = await session.get(Checkout, order.checkout_id)
    if checkout is None or checkout.stripe_payment_intent_id is None:
        raise Conflict("This order wasn't paid through checkout")
    if amount_cents == remaining:
        change_order_status(order, OrderStatus.refunded)
    from_seller = min(amount_cents, order.seller_payout_cents - order.seller_refunded_cents)
    gateway = get_gateway()
    if order.stripe_transfer_id is not None and from_seller > 0:
        await gateway.reverse_transfer(key, order.stripe_transfer_id, from_seller)
    order.stripe_refund_id = await gateway.refund(key, checkout.stripe_payment_intent_id, amount_cents)
    order.refunded_cents += amount_cents
    order.seller_refunded_cents += from_seller
    queue_email(session, order.buyer_id, "order_refunded", **order_email(order), refund_cents=amount_cents)


async def ship_order(session: AsyncSession, current: CurrentUser, order_id: uuid.UUID,
                     tracking_number: str | None) -> Order:
    order = await _get_party_order(session, current, order_id, seller=True)
    change_order_status(order, OrderStatus.shipped)
    order.shipped_at = datetime.now(timezone.utc)
    order.tracking_number = tracking_number
    if settings.payout_on == "shipped":
        await pay_out(session, order)
    queue_email(session, order.buyer_id, "order_shipped", **order_email(order),
                tracking_number=tracking_number or "")
    await session.flush()
    return order


def mark_delivered(order: Order, now: datetime) -> None:
    change_order_status(order, OrderStatus.delivered)
    order.delivered_at = now


async def confirm_delivery(session: AsyncSession, current: CurrentUser, order_id: uuid.UUID) -> Order:
    order = await _get_party_order(session, current, order_id, seller=False)
    mark_delivered(order, datetime.now(timezone.utc))
    # The cards arrived after all, so a "not received" issue is over.
    issue = await session.scalar(select(OrderIssue).where(
        OrderIssue.order_id == order.id, OrderIssue.status != IssueStatus.resolved,
        OrderIssue.reason == IssueReason.not_received).with_for_update())
    if issue is not None:
        close_issue(issue, IssueResolution.withdrawn, current.id)
    await session.flush()
    await pay_out_if_settled(session, order)
    await session.flush()
    return order


async def refund_order(session: AsyncSession, current: CurrentUser, order_id: uuid.UUID) -> Order:
    """The seller cancels an order they haven't shipped; the buyer gets the
    rest of the order's total back. The copies stay sold (the seller usually
    can't fill the order), so relist them if that's wrong."""
    order = await _get_party_order(session, current, order_id, seller=True)
    if order.status != OrderStatus.paid:
        raise Conflict(f"A {order.status.value} order can't be cancelled; refund it through its issue instead")
    amount = order.total_cents - order.refunded_cents
    await refund(session, order, amount, str(order.id))
    # An issue the buyer opened is settled by this too.
    issue = await session.scalar(select(OrderIssue).where(
        OrderIssue.order_id == order.id, OrderIssue.status != IssueStatus.resolved).with_for_update())
    if issue is not None:
        close_issue(issue, IssueResolution.refunded, current.id)
        issue.refund_cents += amount
    await session.flush()
    return order
