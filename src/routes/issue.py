import uuid

from fastapi import APIRouter, status

from auth.auth import AuthUser
from models.database import IssueStatus
from models.model import Page
from models.object_models.issues import (
    IssueCreate,
    IssueDetail,
    IssueMessageCreate,
    IssueRead,
    IssueRefund,
    IssueRole,
)
from services.issues import issues
from services.persistence.database import SessionDep
from utils import limits
from utils.pagination import CursorParam, LimitParam

router = APIRouter(tags=["issues"])


@router.post("/orders/{order_id}/issues", status_code=status.HTTP_201_CREATED, dependencies=[limits.ISSUES])
async def open_issue(order_id: uuid.UUID, request: IssueCreate, current: AuthUser,
                     session: SessionDep) -> IssueDetail:
    """Buyer: report a problem with a paid order. The seller is emailed."""
    return issues.to_detail(await issues.open_issue(session, current, order_id, request))


@router.get("/orders/{order_id}/issues")
async def list_order_issues(order_id: uuid.UUID, current: AuthUser, session: SessionDep) -> list[IssueRead]:
    """An order's issues, newest first (buyer or seller)."""
    return [issues.to_read(i) for i in await issues.get_order_issues(session, current, order_id)]


@router.get("/issues")
async def list_my_issues(
    current: AuthUser, session: SessionDep, role: IssueRole = IssueRole.buyer,
    status: IssueStatus | None = None, limit: LimitParam = 50, cursor: CursorParam = None,
) -> Page[IssueRead]:
    """Issues the caller opened as a buyer, or (role=seller) that were opened on their sales."""
    found, next_cursor = await issues.get_my_issues(session, current, role, status, limit, cursor)
    return Page(items=[issues.to_read(i) for i in found], next_cursor=next_cursor)


@router.get("/issues/{issue_id}")
async def read_issue(issue_id: uuid.UUID, current: AuthUser, session: SessionDep) -> IssueDetail:
    return issues.to_detail(await issues.get_issue(session, current, issue_id))


@router.post("/issues/{issue_id}/messages", dependencies=[limits.ISSUES])
async def add_message(issue_id: uuid.UUID, request: IssueMessageCreate, current: AuthUser,
                      session: SessionDep) -> IssueDetail:
    return issues.to_detail(await issues.add_message(session, current, issue_id, request.body))


@router.post("/issues/{issue_id}/refund", dependencies=[limits.ORDER_ACTIONS])
async def refund_issue(issue_id: uuid.UUID, request: IssueRefund, current: AuthUser,
                       session: SessionDep) -> IssueDetail:
    """Seller: refund all (default) or part of the order, resolving the issue."""
    return issues.to_detail(await issues.refund_issue(session, current, issue_id, request.amount_cents))


@router.post("/issues/{issue_id}/escalate", dependencies=[limits.ISSUES])
async def escalate_issue(issue_id: uuid.UUID, current: AuthUser, session: SessionDep) -> IssueDetail:
    """Ask a moderator to decide: the seller any time, the buyer once escalate_after has passed."""
    return issues.to_detail(await issues.escalate_issue(session, current, issue_id))


@router.post("/issues/{issue_id}/withdraw", dependencies=[limits.ISSUES])
async def withdraw_issue(issue_id: uuid.UUID, current: AuthUser, session: SessionDep) -> IssueDetail:
    """Buyer: close the issue without a refund."""
    return issues.to_detail(await issues.withdraw_issue(session, current, issue_id))
