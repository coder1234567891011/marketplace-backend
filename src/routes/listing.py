import uuid

from fastapi import APIRouter, status

from auth.auth import AuthUser, OptionalUser
from models.model import Page
from models.object_models.listings import ListingCreate, ListingRead, ListingUpdate
from services.listings.listings import create_listing, get_listing, get_user_listings, remove_listing, update_listing
from services.persistence.database import SessionDep
from utils import limits
from utils.pagination import CursorParam, LimitParam

router = APIRouter(
    prefix="/listings",
    tags=["listings"],
)


@router.get("/user/{user_id}")
async def list_user_listings(
    user_id: uuid.UUID, current: OptionalUser, session: SessionDep,
    limit: LimitParam = 50, cursor: CursorParam = None,
) -> Page[ListingRead]:
    listings, next_cursor = await get_user_listings(session, current, user_id, limit, cursor)
    return Page(items=[ListingRead.model_validate(lst) for lst in listings], next_cursor=next_cursor)


@router.get("/{listing_id}")
async def read_listing(listing_id: uuid.UUID, session: SessionDep) -> ListingRead:
    return ListingRead.model_validate(await get_listing(session, listing_id))


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[limits.LISTING_CREATE])
async def add_listing(request: ListingCreate, current: AuthUser, session: SessionDep) -> ListingRead:
    return ListingRead.model_validate(await create_listing(session, current, request))


@router.patch("/{listing_id}", dependencies=[limits.LISTING_EDIT])
async def edit_listing(
    listing_id: uuid.UUID, request: ListingUpdate, current: AuthUser, session: SessionDep
) -> ListingRead:
    return ListingRead.model_validate(await update_listing(session, current, listing_id, request))


@router.delete("/{listing_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[limits.LISTING_EDIT])
async def delete_listing(listing_id: uuid.UUID, current: AuthUser, session: SessionDep) -> None:
    await remove_listing(session, current, listing_id)
