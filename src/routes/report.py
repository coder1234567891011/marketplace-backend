from fastapi import APIRouter, status

from auth.auth import AuthUser
from models.model import Page
from models.object_models.moderation import ReportCreate, ReportRead
from services.moderation import moderation
from services.persistence.database import SessionDep
from utils import limits
from utils.pagination import CursorParam, LimitParam

router = APIRouter(
    prefix="/reports",
    tags=["reports"],
)


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[limits.REPORTS])
async def report(request: ReportCreate, current: AuthUser, session: SessionDep) -> ReportRead:
    """Flag a listing, user or rating for the moderators."""
    return ReportRead.model_validate(await moderation.create_report(session, current, request))


@router.get("")
async def list_my_reports(current: AuthUser, session: SessionDep, limit: LimitParam = 50,
                          cursor: CursorParam = None) -> Page[ReportRead]:
    found, next_cursor = await moderation.get_my_reports(session, current, limit, cursor)
    return Page(items=[ReportRead.model_validate(r) for r in found], next_cursor=next_cursor)
