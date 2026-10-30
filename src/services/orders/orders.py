"""
Orders: one per seller in a checkout. They're created by checkout
(services/checkout), never directly from client input; afterwards the
seller ships or refunds them and the buyer confirms delivery.
"""
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth.auth import CurrentUser
from models.database import Checkout, Order, OrderItem, OrderStatus, User
from models.object_models.orders import OrderRole
from services.payments.stripe_gateway import get_gateway
from utils.errors import Conflict, Forbidden, NotFound
from utils.pagination import paginate
from utils.settings import settings

# Allowed status changes. pending → cancelled is an unpaid checkout expiring;
# disputed is set by Stripe's dispute webhook and resolved outside the API for now.
TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.pending: {OrderStatus.paid, OrderStatus.cancelled},
    OrderStatus.paid: {OrderStatus.shipped, OrderStatus.refunded, OrderStatus.disputed},
    OrderStatus.shipped: {OrderStatus.delivered, OrderStatus.disputed},
    OrderStatus.delivered: {OrderStatus.disputed},
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


async def pay_out(session: AsyncSession, order: Order) -> None:
    """Transfer the seller's share of a paid order to their Stripe account.
    Safe to retry: the transfer's idempotency key is the order id."""
    if order.stripe_transfer_id is not None or order.seller_payout_cents <= 0:
        return
    checkout = await session.get(Checkout, order.checkout_id)
    seller = await session.get(User, order.seller_id)
    if checkout is None or checkout.stripe_charge_id is None or seller is None:
        raise Conflict("This order wasn't paid through checkout")
    if seller.stripe_connect_account_id is None:
        raise Conflict("Set up payouts before shipping (POST /v1/sellers/me/onboarding)")
    order.stripe_transfer_id = await get_gateway().transfer(
        str(order.id), order.seller_payout_cents, order.currency, seller.stripe_connect_account_id,
        checkout.stripe_charge_id, transfer_group=str(checkout.id))
    order.paid_out_at = datetime.now(timezone.utc)


async def ship_order(session: AsyncSession, current: CurrentUser, order_id: uuid.UUID,
                     tracking_number: str | None) -> Order:
    order = await _get_party_order(session, current, order_id, seller=True)
    change_order_status(order, OrderStatus.shipped)
    order.shipped_at = datetime.now(timezone.utc)
    order.tracking_number = tracking_number
    if settings.payout_on == "shipped":
        await pay_out(session, order)
    await session.flush()
    return order


async def confirm_delivery(session: AsyncSession, current: CurrentUser, order_id: uuid.UUID) -> Order:
    order = await _get_party_order(session, current, order_id, seller=False)
    change_order_status(order, OrderStatus.delivered)
    order.delivered_at = datetime.now(timezone.utc)
    await session.flush()
    return order


async def refund_order(session: AsyncSession, current: CurrentUser, order_id: uuid.UUID) -> Order:
    """The seller cancels an order they haven't shipped; the buyer gets the
    order's whole total back. The copies stay sold (the seller usually
    can't fill the order), so relist them if that's wrong."""
    order = await _get_party_order(session, current, order_id, seller=True)
    change_order_status(order, OrderStatus.refunded)
    checkout = await session.get(Checkout, order.checkout_id)
    if checkout is None or checkout.stripe_payment_intent_id is None:
        raise Conflict("This order wasn't paid through checkout")
    gateway = get_gateway()
    if order.stripe_transfer_id is not None:
        await gateway.reverse_transfer(str(order.id), order.stripe_transfer_id)
    order.stripe_refund_id = await gateway.refund(str(order.id), checkout.stripe_payment_intent_id,
                                                  order.total_cents)
    await session.flush()
    return order
