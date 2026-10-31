"""
Reports and moderation.

Any user can report a listing, user or rating. Moderators (users.is_admin)
work through open reports and escalated issues, suspend or ban accounts,
take down listings and ratings, and decide issues. Everything a moderator
does is written to moderation_actions, and acting on a target also closes
the open reports about it.
"""
import uuid
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth.auth import CurrentUser
from models.database import (
    IssueStatus,
    Listing,
    ListingStatus,
    ModerationAction,
    ModerationActionType,
    OrderIssue,
    Rating,
    Report,
    ReportStatus,
    ReportTarget,
    User,
)
from models.object_models.moderation import (
    AccountStatusUpdate,
    IssueDecision,
    ReportCreate,
    ReportResolve,
)
from services.email.outbox import queue_email
from services.issues.issues import load_issue, post_message, settle
from services.listings.listings import change_status
from services.ratings.ratings import refresh_rating_stats
from services.user.user import require_profile
from utils.errors import BadRequest, Conflict, NotFound
from utils.pagination import paginate


# ----------------------------------------------------------------
# Reports (any user)
# ----------------------------------------------------------------
async def _target_owner(session: AsyncSession, target_type: ReportTarget, target_id: uuid.UUID) -> uuid.UUID:
    """Who the reported thing belongs to; NotFound if it doesn't exist."""
    owner: uuid.UUID | None
    if target_type == ReportTarget.listing:
        owner = await session.scalar(select(Listing.seller_id).where(Listing.id == target_id))
    elif target_type == ReportTarget.user:
        owner = await session.scalar(select(User.id).where(User.id == target_id))
    else:
        owner = await session.scalar(select(Rating.rater_id).where(Rating.id == target_id))
    if owner is None:
        raise NotFound(f"{target_type.value.capitalize()} not found")
    return owner


async def create_report(session: AsyncSession, current: CurrentUser, request: ReportCreate) -> Report:
    await require_profile(session, current)
    if await _target_owner(session, request.target_type, request.target_id) == current.id:
        raise BadRequest("You can't report yourself or your own listings and ratings")
    duplicate = await session.scalar(select(Report.id).where(
        Report.reporter_id == current.id, Report.target_type == request.target_type,
        Report.target_id == request.target_id, Report.status == ReportStatus.open))
    if duplicate is not None:
        raise Conflict("You've already reported this", code="already_reported")
    report = Report(reporter_id=current.id, **request.model_dump())
    session.add(report)
    await session.flush()
    return report


async def get_my_reports(session: AsyncSession, current: CurrentUser, limit: int,
                         cursor: str | None) -> tuple[Sequence[Report], str | None]:
    return await paginate(session, select(Report).where(Report.reporter_id == current.id), Report, limit, cursor)


# ----------------------------------------------------------------
# Moderators
# ----------------------------------------------------------------
async def _log(session: AsyncSession, admin: CurrentUser, action: ModerationActionType, target_id: uuid.UUID,
               note: str | None, **details: Any) -> None:
    session.add(ModerationAction(admin_id=admin.id, action=action, target_id=target_id, note=note, details=details))


async def _action_reports(session: AsyncSession, admin: CurrentUser, target_type: ReportTarget,
                          target_id: uuid.UUID, note: str | None) -> None:
    await session.execute(update(Report).where(
        Report.target_type == target_type, Report.target_id == target_id, Report.status == ReportStatus.open,
    ).values(status=ReportStatus.actioned, resolved_by=admin.id, resolution_note=note,
             resolved_at=datetime.now(timezone.utc)))


async def get_reports(session: AsyncSession, status: ReportStatus | None, target_type: ReportTarget | None,
                      limit: int, cursor: str | None) -> tuple[Sequence[Report], str | None]:
    stmt = select(Report)
    if status is not None:
        stmt = stmt.where(Report.status == status)
    if target_type is not None:
        stmt = stmt.where(Report.target_type == target_type)
    return await paginate(session, stmt, Report, limit, cursor)


async def resolve_report(session: AsyncSession, admin: CurrentUser, report_id: uuid.UUID,
                         request: ReportResolve) -> Report:
    report = await session.scalar(select(Report).where(Report.id == report_id).with_for_update())
    if report is None:
        raise NotFound("Report not found")
    if report.status != ReportStatus.open:
        raise Conflict("This report is already resolved")
    report.status, report.resolved_by, report.resolution_note = request.status, admin.id, request.note
    report.resolved_at = datetime.now(timezone.utc)
    await _log(session, admin, ModerationActionType.resolve_report, report.id, request.note,
               status=request.status.value)
    await session.flush()
    return report


async def set_account_status(session: AsyncSession, admin: CurrentUser, user_id: uuid.UUID,
                             request: AccountStatusUpdate) -> User:
    """Suspended users can sign in and read but not change anything; banned
    users can't use the API at all. Either way their listings stop being
    for sale (services/listings: for_sale_conditions)."""
    if user_id == admin.id:
        raise BadRequest("You can't change your own account status")
    user = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise NotFound("User not found")
    if user.account_status != request.account_status:
        user.account_status = request.account_status
        await _log(session, admin, ModerationActionType.set_account_status, user.id, request.note,
                   account_status=request.account_status.value)
        queue_email(session, user.id, "account_status", account_status=request.account_status.value,
                    note=request.note or "")
    await _action_reports(session, admin, ReportTarget.user, user.id, request.note)
    await session.flush()
    return user


async def remove_listing(session: AsyncSession, admin: CurrentUser, listing_id: uuid.UUID,
                         note: str | None) -> None:
    listing = await session.scalar(select(Listing).where(Listing.id == listing_id).with_for_update())
    if listing is None:
        raise NotFound("Listing not found")
    if listing.status == ListingStatus.pending_sale:
        raise Conflict("A buyer is paying for this listing; try again once their checkout ends")
    if listing.status == ListingStatus.active:
        change_status(listing, ListingStatus.removed)
        await _log(session, admin, ModerationActionType.remove_listing, listing.id, note)
    await _action_reports(session, admin, ReportTarget.listing, listing.id, note)
    await session.flush()


async def remove_rating(session: AsyncSession, admin: CurrentUser, rating_id: uuid.UUID, note: str | None) -> None:
    rating = await session.get(Rating, rating_id)
    if rating is None:
        raise NotFound("Rating not found")
    await _log(session, admin, ModerationActionType.remove_rating, rating.id, note,
               ratee_id=str(rating.ratee_id), rating=rating.rating, comment=rating.comment)
    await _action_reports(session, admin, ReportTarget.rating, rating.id, note)
    await session.delete(rating)
    await session.flush()
    await refresh_rating_stats(session, rating.ratee_id, rating.role)
    await session.flush()


async def get_issues(session: AsyncSession, status: IssueStatus | None, limit: int,
                     cursor: str | None) -> tuple[Sequence[OrderIssue], str | None]:
    stmt = select(OrderIssue)
    if status is not None:
        stmt = stmt.where(OrderIssue.status == status)
    return await paginate(session, stmt, OrderIssue, limit, cursor)


async def get_issue(session: AsyncSession, issue_id: uuid.UUID, lock: bool = False) -> OrderIssue:
    issue = await load_issue(session, issue_id, lock)
    if issue is None:
        raise NotFound("Issue not found")
    return issue


async def message_issue(session: AsyncSession, admin: CurrentUser, issue_id: uuid.UUID, body: str) -> OrderIssue:
    issue = await get_issue(session, issue_id, lock=True)
    await post_message(session, admin.id, issue, body)
    session.expire(issue, ["messages"])
    return await get_issue(session, issue_id)


async def decide_issue(session: AsyncSession, admin: CurrentUser, issue_id: uuid.UUID,
                       request: IssueDecision) -> OrderIssue:
    """Resolve an open or escalated issue, refunding request.refund_cents."""
    issue = await get_issue(session, issue_id, lock=True)
    if request.note:
        # Emailed with the outcome below rather than on its own.
        await post_message(session, admin.id, issue, request.note, notify=False)
    await settle(session, issue, admin.id, request.refund_cents, request.note)
    await _log(session, admin, ModerationActionType.resolve_issue, issue.id, request.note,
               order_id=str(issue.order_id), refund_cents=request.refund_cents)
    await session.flush()
    session.expire(issue, ["messages"])
    return await get_issue(session, issue_id)


async def get_actions(session: AsyncSession, target_id: uuid.UUID | None, limit: int,
                      cursor: str | None) -> tuple[Sequence[ModerationAction], str | None]:
    stmt = select(ModerationAction)
    if target_id is not None:
        stmt = stmt.where(ModerationAction.target_id == target_id)
    return await paginate(session, stmt, ModerationAction, limit, cursor)
