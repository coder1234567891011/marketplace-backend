"""
Order issues: buyer protection after payment.

  1. The buyer opens an issue on a paid, shipped or delivered order (until
     settings.issue_window_days after delivery), with a first message.
  2. Buyer and seller message each other. The seller can refund all or part
     of the order, which resolves it; the buyer can withdraw it.
  3. If it isn't sorted out, the seller (any time) or the buyer (after
     settings.issue_seller_days) escalates it, and a moderator decides
     (services/moderation): a refund of any amount, or none.

While an issue is unresolved the order isn't auto-delivered and, with
payout_on=delivered, the seller isn't paid.
"""
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth.auth import CurrentUser
from models.database import (
    IssueMessage,
    IssueResolution,
    IssueStatus,
    Order,
    OrderIssue,
    OrderStatus,
)
from models.object_models.issues import IssueCreate, IssueDetail, IssueMessageRead, IssueRead, IssueRole
from services.email.outbox import queue_email
from services.orders.orders import close_issue, order_email, pay_out_if_settled, refund
from utils.errors import Conflict, Forbidden, NotFound
from utils.pagination import paginate
from utils.settings import settings

OPEN_FOR_ISSUES = (OrderStatus.paid, OrderStatus.shipped, OrderStatus.delivered)
REASONS = {"not_received": "not received", "not_as_described": "not as described", "other": "other"}


def escalate_after(issue: OrderIssue) -> datetime:
    return issue.created_at + timedelta(days=settings.issue_seller_days)


def _fields(issue: OrderIssue) -> dict[str, Any]:
    fields = {name: getattr(issue, name) for name in IssueRead.model_fields if name != "escalate_after"}
    return fields | {"escalate_after": escalate_after(issue)}


def to_read(issue: OrderIssue) -> IssueRead:
    return IssueRead(**_fields(issue))


def to_detail(issue: OrderIssue) -> IssueDetail:
    parties = (issue.buyer_id, issue.seller_id)
    messages = [IssueMessageRead.model_validate(m).model_copy(update={"from_moderator": m.author_id not in parties})
                for m in issue.messages]
    return IssueDetail(**_fields(issue), messages=messages)


async def load_issue(session: AsyncSession, issue_id: uuid.UUID, lock: bool = False) -> OrderIssue | None:
    stmt = select(OrderIssue).where(OrderIssue.id == issue_id).options(selectinload(OrderIssue.messages))
    if lock:
        stmt = stmt.with_for_update(of=OrderIssue)
    return await session.scalar(stmt)


async def _party_issue(session: AsyncSession, current: CurrentUser, issue_id: uuid.UUID,
                       lock: bool = False) -> OrderIssue:
    issue = await load_issue(session, issue_id, lock)
    if issue is None or current.id not in (issue.buyer_id, issue.seller_id):
        raise NotFound("Issue not found")
    return issue


def _require_unresolved(issue: OrderIssue) -> None:
    if issue.status == IssueStatus.resolved:
        raise Conflict("This issue is already resolved")


async def _locked_order(session: AsyncSession, order_id: uuid.UUID) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    assert order is not None
    return order


def _email_data(issue: OrderIssue, order: Order) -> dict[str, str | int]:
    return order_email(order) | {"reason": REASONS[issue.reason.value]}


# ----------------------------------------------------------------
# Reading
# ----------------------------------------------------------------
async def get_issue(session: AsyncSession, current: CurrentUser, issue_id: uuid.UUID) -> OrderIssue:
    return await _party_issue(session, current, issue_id)


async def get_order_issues(session: AsyncSession, current: CurrentUser, order_id: uuid.UUID) -> Sequence[OrderIssue]:
    order = await session.get(Order, order_id)
    if order is None or current.id not in (order.buyer_id, order.seller_id):
        raise NotFound("Order not found")
    return (await session.scalars(select(OrderIssue).where(OrderIssue.order_id == order_id)
                                  .order_by(OrderIssue.created_at.desc()))).all()


async def get_my_issues(session: AsyncSession, current: CurrentUser, role: IssueRole, status: IssueStatus | None,
                        limit: int, cursor: str | None) -> tuple[Sequence[OrderIssue], str | None]:
    party = OrderIssue.buyer_id if role == IssueRole.buyer else OrderIssue.seller_id
    stmt = select(OrderIssue).where(party == current.id)
    if status is not None:
        stmt = stmt.where(OrderIssue.status == status)
    return await paginate(session, stmt, OrderIssue, limit, cursor)


# ----------------------------------------------------------------
# Buyer and seller
# ----------------------------------------------------------------
async def open_issue(session: AsyncSession, current: CurrentUser, order_id: uuid.UUID,
                     request: IssueCreate) -> OrderIssue:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None or current.id not in (order.buyer_id, order.seller_id):
        raise NotFound("Order not found")
    if order.buyer_id != current.id:
        raise Forbidden("Only the buyer can do this")
    if order.status not in OPEN_FOR_ISSUES:
        raise Conflict(f"A {order.status.value} order can't have an issue opened")
    now = datetime.now(timezone.utc)
    if order.delivered_at is not None and now > order.delivered_at + timedelta(days=settings.issue_window_days):
        raise Conflict(f"Issues can only be opened within {settings.issue_window_days} days of delivery")
    if await session.scalar(select(OrderIssue.id).where(
            OrderIssue.order_id == order.id, OrderIssue.status != IssueStatus.resolved)) is not None:
        raise Conflict("This order already has an open issue", code="issue_open")

    issue = OrderIssue(order_id=order.id, buyer_id=order.buyer_id, seller_id=order.seller_id, reason=request.reason)
    session.add(issue)
    await session.flush()
    session.add(IssueMessage(issue_id=issue.id, author_id=current.id, body=request.message))
    queue_email(session, order.seller_id, "issue_opened", **_email_data(issue, order),
                seller_days=settings.issue_seller_days)
    await session.flush()
    loaded = await load_issue(session, issue.id)
    assert loaded is not None
    return loaded


async def post_message(session: AsyncSession, author_id: uuid.UUID, issue: OrderIssue, body: str,
                       notify: bool = True) -> None:
    """Add a message and (notify) email everyone else on the issue."""
    _require_unresolved(issue)
    session.add(IssueMessage(issue_id=issue.id, author_id=author_id, body=body))
    order = await session.get(Order, issue.order_id)
    assert order is not None
    for party in (issue.buyer_id, issue.seller_id):
        if notify and party != author_id:
            queue_email(session, party, "issue_message", **_email_data(issue, order), body=body)
    await session.flush()


async def add_message(session: AsyncSession, current: CurrentUser, issue_id: uuid.UUID, body: str) -> OrderIssue:
    issue = await _party_issue(session, current, issue_id, lock=True)
    await post_message(session, current.id, issue, body)
    return await _reloaded(session, issue)


async def settle(session: AsyncSession, issue: OrderIssue, resolved_by: uuid.UUID,
                 refund_cents: int | None, note: str | None = None) -> None:
    """Resolve an issue: refund refund_cents (None: everything left on the
    order; 0: nothing), then pay the seller if that's now due."""
    _require_unresolved(issue)
    order = await _locked_order(session, issue.order_id)
    amount = order.total_cents - order.refunded_cents if refund_cents is None else refund_cents
    if amount > 0:
        await refund(session, order, amount, f"issue-{issue.id}")
        issue.refund_cents += amount
    close_issue(issue, IssueResolution.refunded if amount > 0 else IssueResolution.no_refund, resolved_by)
    await session.flush()
    await pay_out_if_settled(session, order)
    _email_resolved(session, issue, order, note)
    await session.flush()


def _email_resolved(session: AsyncSession, issue: OrderIssue, order: Order, note: str | None = None) -> None:
    assert issue.resolution is not None
    for party in (issue.buyer_id, issue.seller_id):
        queue_email(session, party, "issue_resolved", **_email_data(issue, order),
                    resolution=issue.resolution.value, refund_cents=issue.refund_cents, note=note or "")


async def refund_issue(session: AsyncSession, current: CurrentUser, issue_id: uuid.UUID,
                       amount_cents: int | None) -> OrderIssue:
    """The seller refunds the buyer, resolving the issue."""
    issue = await _party_issue(session, current, issue_id, lock=True)
    if current.id != issue.seller_id:
        raise Forbidden("Only the seller can do this")
    await settle(session, issue, current.id, amount_cents)
    return await _reloaded(session, issue)


async def withdraw_issue(session: AsyncSession, current: CurrentUser, issue_id: uuid.UUID) -> OrderIssue:
    issue = await _party_issue(session, current, issue_id, lock=True)
    if current.id != issue.buyer_id:
        raise Forbidden("Only the buyer can do this")
    _require_unresolved(issue)
    close_issue(issue, IssueResolution.withdrawn, current.id)
    order = await _locked_order(session, issue.order_id)
    await session.flush()
    await pay_out_if_settled(session, order)
    _email_resolved(session, issue, order)
    await session.flush()
    return await _reloaded(session, issue)


async def escalate_issue(session: AsyncSession, current: CurrentUser, issue_id: uuid.UUID) -> OrderIssue:
    issue = await _party_issue(session, current, issue_id, lock=True)
    if issue.status != IssueStatus.open:
        raise Conflict(f"An {issue.status.value} issue can't be escalated")
    now = datetime.now(timezone.utc)
    if current.id == issue.buyer_id and now < escalate_after(issue):
        raise Conflict(f"The seller has until {escalate_after(issue).isoformat()} to respond first",
                       code="too_early")
    issue.status, issue.escalated_at = IssueStatus.escalated, now
    order = await session.get(Order, issue.order_id)
    assert order is not None
    for party in (issue.buyer_id, issue.seller_id):
        queue_email(session, party, "issue_escalated", **_email_data(issue, order))
    await session.flush()
    return await _reloaded(session, issue)


async def _reloaded(session: AsyncSession, issue: OrderIssue) -> OrderIssue:
    session.expire(issue, ["messages"])
    loaded = await load_issue(session, issue.id)
    assert loaded is not None
    return loaded
