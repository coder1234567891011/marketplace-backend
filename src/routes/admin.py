"""Moderator endpoints. Moderators are users with users.is_admin set (see README "Moderation")."""
import uuid

from fastapi import APIRouter, status

from auth.auth import AdminUser
from models.database import IssueStatus, ReportStatus, ReportTarget
from models.model import Page
from models.object_models.issues import IssueDetail, IssueMessageCreate, IssueRead
from models.object_models.moderation import (
    AccountStatusUpdate,
    IssueDecision,
    ModerationActionRead,
    ModerationNote,
    ReportAdminRead,
    ReportResolve,
)
from models.object_models.user import UserRead
from services.issues.issues import to_detail, to_read
from services.moderation import moderation
from services.persistence.database import SessionDep
from utils.pagination import CursorParam, LimitParam

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
)


@router.get("/reports")
async def list_reports(
    admin: AdminUser, session: SessionDep, status: ReportStatus | None = ReportStatus.open,
    target_type: ReportTarget | None = None, limit: LimitParam = 50, cursor: CursorParam = None,
) -> Page[ReportAdminRead]:
    """Reports, newest first; open ones by default."""
    found, next_cursor = await moderation.get_reports(session, status, target_type, limit, cursor)
    return Page(items=[ReportAdminRead.model_validate(r) for r in found], next_cursor=next_cursor)


@router.post("/reports/{report_id}/resolve")
async def resolve_report(report_id: uuid.UUID, request: ReportResolve, admin: AdminUser,
                         session: SessionDep) -> ReportAdminRead:
    """Close a report. Acting on its target (below) closes its reports as actioned anyway."""
    return ReportAdminRead.model_validate(await moderation.resolve_report(session, admin, report_id, request))


@router.put("/users/{user_id}/status")
async def set_account_status(user_id: uuid.UUID, request: AccountStatusUpdate, admin: AdminUser,
                             session: SessionDep) -> UserRead:
    """Suspend (read-only), ban, or reinstate (active) an account. The user is emailed."""
    return UserRead.model_validate(await moderation.set_account_status(session, admin, user_id, request))


@router.post("/listings/{listing_id}/remove", status_code=status.HTTP_204_NO_CONTENT)
async def remove_listing(listing_id: uuid.UUID, request: ModerationNote, admin: AdminUser,
                         session: SessionDep) -> None:
    await moderation.remove_listing(session, admin, listing_id, request.note)


@router.post("/ratings/{rating_id}/remove", status_code=status.HTTP_204_NO_CONTENT)
async def remove_rating(rating_id: uuid.UUID, request: ModerationNote, admin: AdminUser,
                        session: SessionDep) -> None:
    """Delete a rating (e.g. an abusive comment); the user's average is recalculated."""
    await moderation.remove_rating(session, admin, rating_id, request.note)


@router.get("/issues")
async def list_issues(
    admin: AdminUser, session: SessionDep, status: IssueStatus | None = IssueStatus.escalated,
    limit: LimitParam = 50, cursor: CursorParam = None,
) -> Page[IssueRead]:
    """Order issues, newest first; escalated ones by default."""
    found, next_cursor = await moderation.get_issues(session, status, limit, cursor)
    return Page(items=[to_read(i) for i in found], next_cursor=next_cursor)


@router.get("/issues/{issue_id}")
async def read_issue(issue_id: uuid.UUID, admin: AdminUser, session: SessionDep) -> IssueDetail:
    return to_detail(await moderation.get_issue(session, issue_id))


@router.post("/issues/{issue_id}/messages")
async def message_issue(issue_id: uuid.UUID, request: IssueMessageCreate, admin: AdminUser,
                        session: SessionDep) -> IssueDetail:
    """Ask the buyer or seller something; both are emailed."""
    return to_detail(await moderation.message_issue(session, admin, issue_id, request.body))


@router.post("/issues/{issue_id}/resolve")
async def decide_issue(issue_id: uuid.UUID, request: IssueDecision, admin: AdminUser,
                       session: SessionDep) -> IssueDetail:
    """Decide an issue: refund refund_cents to the buyer (0 for none)."""
    return to_detail(await moderation.decide_issue(session, admin, issue_id, request))


@router.get("/actions")
async def list_actions(admin: AdminUser, session: SessionDep, target_id: uuid.UUID | None = None,
                       limit: LimitParam = 50, cursor: CursorParam = None) -> Page[ModerationActionRead]:
    """The moderation log, newest first, optionally for one user, listing, rating or issue."""
    found, next_cursor = await moderation.get_actions(session, target_id, limit, cursor)
    return Page(items=[ModerationActionRead.model_validate(a) for a in found], next_cursor=next_cursor)
