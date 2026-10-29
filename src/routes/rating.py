import uuid

from fastapi import APIRouter, status

from auth.auth import AuthUser
from models.model import Page
from models.object_models.ratings import RatingCreate, RatingRead
from services.persistence.database import SessionDep
from services.ratings.ratings import create_rating, get_rating, get_user_ratings
from utils import limits
from utils.pagination import CursorParam, LimitParam

router = APIRouter(
    prefix="/ratings",
    tags=["ratings"],
)


@router.get("/user/{user_id}")
async def list_user_ratings(
    user_id: uuid.UUID, session: SessionDep, limit: LimitParam = 50, cursor: CursorParam = None
) -> Page[RatingRead]:
    ratings, next_cursor = await get_user_ratings(session, user_id, limit, cursor)
    return Page(items=[RatingRead.model_validate(r) for r in ratings], next_cursor=next_cursor)


@router.get("/{rating_id}")
async def read_rating(rating_id: uuid.UUID, session: SessionDep) -> RatingRead:
    return RatingRead.model_validate(await get_rating(session, rating_id))


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[limits.RATINGS])
async def add_rating(request: RatingCreate, current: AuthUser, session: SessionDep) -> RatingRead:
    return RatingRead.model_validate(await create_rating(session, current, request))
