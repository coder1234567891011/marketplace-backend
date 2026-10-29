import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth.auth import CurrentUser
from models.database import Listing, ListingStatus
from models.object_models.listings import ListingCreate, ListingUpdate
from services.cards.cards import get_card
from services.collections.collections import get_own_collection_item
from services.user.user import require_profile
from utils.errors import BadRequest, Conflict, Forbidden, NotFound
from utils.pagination import paginate


def _with_relations():  # type: ignore[no-untyped-def]
    return (selectinload(Listing.seller), selectinload(Listing.card))


async def get_user_listings(
    session: AsyncSession, current: CurrentUser | None, user_id: uuid.UUID, limit: int, cursor: str | None
) -> tuple[Sequence[Listing], str | None]:
    stmt = select(Listing).where(Listing.seller_id == user_id).options(*_with_relations())
    # Sellers see all their listings; everyone else sees only what's for sale.
    if current is None or current.id != user_id:
        stmt = stmt.where(Listing.status == ListingStatus.active)
    return await paginate(session, stmt, Listing, limit, cursor)


async def get_listing(session: AsyncSession, listing_id: uuid.UUID) -> Listing:
    listing = await session.scalar(select(Listing).where(Listing.id == listing_id).options(*_with_relations()))
    if listing is None:
        raise NotFound("Listing not found")
    return listing


async def _get_own_listing(session: AsyncSession, current: CurrentUser, listing_id: uuid.UUID) -> Listing:
    listing = await get_listing(session, listing_id)
    if listing.seller_id != current.id:
        raise Forbidden("You can only change your own listings")
    return listing


async def create_listing(session: AsyncSession, current: CurrentUser, request: ListingCreate) -> Listing:
    await require_profile(session, current)
    await get_card(session, request.card_id)
    if request.collection_item_id is not None:
        item = await get_own_collection_item(session, current, request.collection_item_id)
        if item.card_id != request.card_id:
            raise BadRequest("Collection item is a different card")
        if request.quantity_available > item.quantity:
            raise BadRequest("Listing quantity exceeds the quantity in your collection")
    listing = Listing(seller_id=current.id, **request.model_dump())
    session.add(listing)
    await session.flush()
    return await get_listing(session, listing.id)


async def update_listing(
    session: AsyncSession, current: CurrentUser, listing_id: uuid.UUID, request: ListingUpdate
) -> Listing:
    listing = await _get_own_listing(session, current, listing_id)
    if listing.status != ListingStatus.active:
        raise Conflict(f"Listing is {listing.status.value} and can't be edited")
    for field, value in request.model_dump(exclude_unset=True).items():
        setattr(listing, field, value)
    await session.flush()
    return listing


async def remove_listing(session: AsyncSession, current: CurrentUser, listing_id: uuid.UUID) -> None:
    """Soft delete: orders reference listings, so rows are kept with status=removed."""
    listing = await _get_own_listing(session, current, listing_id)
    if listing.status not in (ListingStatus.active, ListingStatus.removed):
        raise Conflict(f"Listing is {listing.status.value} and can't be removed")
    listing.status = ListingStatus.removed
    await session.flush()
