import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status

from auth.auth import AuthUser, OptionalUser
from models.database import CardCondition, CardFinish
from models.model import Page
from models.object_models.listings import ListingCreate, ListingGroup, ListingRead, ListingUpdate
from services.listings.listings import (
    StorefrontSort,
    create_listing,
    get_card_listing_groups,
    get_card_listings,
    get_listing,
    get_user_listings,
    remove_listing,
    update_listing,
)
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
    q: Annotated[str | None, Query(max_length=100, description="Part of the card name")] = None,
    sort: StorefrontSort = "newest",
    limit: LimitParam = 50, cursor: CursorParam = None,
) -> Page[ListingRead]:
    """A seller's storefront. The seller also sees their sold and removed listings."""
    listings, next_cursor = await get_user_listings(session, current, user_id, limit, cursor, q=q, sort=sort)
    return Page(items=[ListingRead.model_validate(lst) for lst in listings], next_cursor=next_cursor)


@router.get("/card/{card_id}")
async def list_card_listings(
    card_id: uuid.UUID, session: SessionDep,
    condition: CardCondition | None = None, finish: CardFinish | None = None,
    language: Annotated[str | None, Query(pattern="^[a-z]{2,3}$")] = None,
    limit: LimitParam = 50, cursor: CursorParam = None,
) -> Page[ListingRead]:
    """Listings of one printing for sale, cheapest first."""
    listings, next_cursor = await get_card_listings(session, card_id, limit, cursor, condition=condition,
                                                    finish=finish, language=language)
    return Page(items=[ListingRead.model_validate(lst) for lst in listings], next_cursor=next_cursor)


@router.get("/card/{card_id}/summary")
async def card_listing_summary(card_id: uuid.UUID, session: SessionDep) -> list[ListingGroup]:
    """How many are for sale and the lowest price, per condition and finish."""
    return await get_card_listing_groups(session, card_id)


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
