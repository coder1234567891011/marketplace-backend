"""
Nightly order upkeep (scripts/order_maintenance.py):
  * shipped orders the buyer never marked received count as delivered after
    settings.auto_deliver_days, unless an issue is open;
  * with payout_on=delivered, sellers of delivered orders with no open issue
    are paid, including payouts that failed earlier.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import IssueStatus, Order, OrderIssue, OrderStatus
from services.orders.orders import mark_delivered, pay_out
from services.payments.stripe_gateway import StripeFailed
from utils.errors import Conflict
from utils.settings import settings

log = logging.getLogger(__name__)


@dataclass
class Maintenance:
    delivered: int = 0
    paid_out: int = 0
    payouts_failed: int = 0


def _no_open_issue():  # type: ignore[no-untyped-def]
    return ~select(OrderIssue.id).where(OrderIssue.order_id == Order.id,
                                        OrderIssue.status != IssueStatus.resolved).exists()


async def run_maintenance(session: AsyncSession, now: datetime) -> Maintenance:
    result = Maintenance()
    stale = (await session.scalars(select(Order).where(
        Order.status == OrderStatus.shipped, Order.shipped_at < now - timedelta(days=settings.auto_deliver_days),
        _no_open_issue()).with_for_update(skip_locked=True))).all()
    for order in stale:
        mark_delivered(order, now)
        result.delivered += 1
    await session.flush()

    if settings.payout_on == "delivered":
        unpaid = (await session.scalars(select(Order).where(
            Order.status == OrderStatus.delivered, Order.stripe_transfer_id.is_(None),
            Order.seller_payout_cents > Order.seller_refunded_cents, _no_open_issue(),
        ).with_for_update(skip_locked=True))).all()
        for order in unpaid:
            try:
                await pay_out(session, order)
                result.paid_out += 1
            except (StripeFailed, Conflict) as e:
                log.warning("Payout for order %s failed: %s", order.id, e)
                result.payouts_failed += 1
        await session.flush()
    return result
