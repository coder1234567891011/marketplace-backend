import uuid
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth.auth import CurrentUser
from models.database import (
    AccountStatus,
    Card,
    CardCondition,
    CardFinish,
    CollectionItem,
    Listing,
    ListingStatus,
    User,
)
from models.object_models.listings import ListingCreate, ListingGroup, ListingUpdate
from services.cards.cards import get_card
from services.collections.collections import check_finish, get_own_collection_item, listed_quantities
from services.user.user import require_profile
from utils.errors import BadRequest, Conflict, Forbidden, NotFound
from utils.pagination import SortKey, keyset_paginate, paginate

StorefrontSort = Literal["newest", "price_asc", "price_desc"]

# Which status changes are allowed. Sellers only ever remove listings; the
# others are made by checkout (Phase 4): reserving a listing for a buyer
# (pending_sale), completing (sold) or releasing it (back to active).
TRANSITIONS: dict[ListingStatus, set[ListingStatus]] = {
    ListingStatus.active: {ListingStatus.pending_sale, ListingStatus.removed},
    ListingStatus.pending_sale: {ListingStatus.active, ListingStatus.sold},
    ListingStatus.sold: set(),
    ListingStatus.removed: set(),
}


def change_status(listing: Listing, new_status: ListingStatus) -> None:
    if new_status not in TRANSITIONS[listing.status]:
        raise Conflict(f"A {listing.status.value} listing can't become {new_status.value}")
    listing.status = new_status


def _with_relations():  # type: ignore[no-untyped-def]
    return (selectinload(Listing.seller), selectinload(Listing.card))


def _for_sale(stmt: Select[tuple[Listing]]) -> Select[tuple[Listing]]:
    """Only active listings from sellers in good standing."""
    return (stmt.join(User, User.id == Listing.seller_id)
            .where(Listing.status == ListingStatus.active, User.account_status == AccountStatus.active))


async def get_user_listings(
    session: AsyncSession, current: CurrentUser | None, user_id: uuid.UUID, limit: int, cursor: str | None,
    q: str | None = None, sort: StorefrontSort = "newest",
) -> tuple[Sequence[Listing], str | None]:
    """A seller's storefront. Sellers see all their listings; everyone else
    sees only what's for sale."""
    stmt = select(Listing).where(Listing.seller_id == user_id).options(*_with_relations())
    if current is None or current.id != user_id:
        stmt = _for_sale(stmt)
    if q:
        escaped = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Listing.card_id.in_(select(Card.id).where(Card.name.ilike(f"%{escaped}%"))))
    if sort == "newest":
        return await paginate(session, stmt, Listing, limit, cursor)
    keys: list[SortKey] = [(Listing.price_cents, int), (Listing.id, uuid.UUID)]
    return await keyset_paginate(session, stmt, keys, limit, cursor, descending=sort == "price_desc")


async def get_card_listings(
    session: AsyncSession, card_id: uuid.UUID, limit: int, cursor: str | None,
    condition: CardCondition | None = None, finish: CardFinish | None = None, language: str | None = None,
) -> tuple[Sequence[Listing], str | None]:
    """Listings of one printing for sale, cheapest first."""
    await get_card(session, card_id)
    stmt = _for_sale(select(Listing).where(Listing.card_id == card_id).options(*_with_relations()))
    if condition:
        stmt = stmt.where(Listing.condition == condition)
    if finish:
        stmt = stmt.where(Listing.finish == finish)
    if language:
        stmt = stmt.where(Listing.language == language)
    keys: list[SortKey] = [(Listing.price_cents, int), (Listing.id, uuid.UUID)]
    return await keyset_paginate(session, stmt, keys, limit, cursor)


async def get_card_listing_groups(session: AsyncSession, card_id: uuid.UUID) -> list[ListingGroup]:
    """What's for sale of one printing, by condition and finish, cheapest first."""
    await get_card(session, card_id)
    stmt = (
        select(Listing.condition, Listing.finish, func.count(), func.sum(Listing.quantity_available),
               func.min(Listing.price_cents))
        .join(User, User.id == Listing.seller_id)
        .where(Listing.card_id == card_id, Listing.status == ListingStatus.active,
               User.account_status == AccountStatus.active)
        .group_by(Listing.condition, Listing.finish)
        .order_by(func.min(Listing.price_cents), Listing.condition, Listing.finish)
    )
    return [ListingGroup(condition=condition, finish=finish, listings=count, quantity=int(quantity),
                         lowest_price_cents=lowest)
            for condition, finish, count, quantity, lowest in await session.execute(stmt)]


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


async def _check_free_copies(
    session: AsyncSession, item: CollectionItem, quantity: int, replacing: Listing | None = None
) -> None:
    """quantity more copies of item must be free (not already listed).
    The caller holds a lock on item."""
    listed = (await listed_quantities(session, [item.id])).get(item.id, 0)
    if replacing is not None and replacing.status in (ListingStatus.active, ListingStatus.pending_sale):
        listed -= replacing.quantity_available
    free = item.quantity - listed
    if quantity > free:
        raise Conflict(f"Only {free} of this card in your collection {'is' if free == 1 else 'are'} free to list")


async def create_listing(session: AsyncSession, current: CurrentUser, request: ListingCreate) -> Listing:
    await require_profile(session, current)
    card = await get_card(session, request.card_id)
    fields = request.model_dump()
    if request.collection_item_id is not None:
        item = await get_own_collection_item(session, current, request.collection_item_id, lock=True)
        if item.card_id != request.card_id:
            raise BadRequest("Collection item is a different card")
        fields["finish"] = request.finish or item.finish
        fields["language"] = request.language or item.language
        if (fields["finish"], fields["language"]) != (item.finish, item.language):
            raise BadRequest("Finish and language must match the collection item")
        if item.condition is not None and request.condition != item.condition:
            raise BadRequest(f"Condition must match the collection item ({item.condition.value})")
        await _check_free_copies(session, item, request.quantity_available)
    fields["finish"] = fields["finish"] or CardFinish.nonfoil
    fields["language"] = fields["language"] or "en"
    check_finish(card, fields["finish"])
    listing = Listing(seller_id=current.id, **fields)
    session.add(listing)
    await session.flush()
    return await get_listing(session, listing.id)


async def update_listing(
    session: AsyncSession, current: CurrentUser, listing_id: uuid.UUID, request: ListingUpdate
) -> Listing:
    listing = await _get_own_listing(session, current, listing_id)
    if listing.status != ListingStatus.active:
        raise Conflict(f"Listing is {listing.status.value} and can't be edited")
    changes = {f: v for f, v in request.model_dump(exclude_unset=True).items() if v is not None or f == "notes"}
    if "quantity_available" in changes and listing.collection_item_id is not None:
        item = await get_own_collection_item(session, current, listing.collection_item_id, lock=True)
        await _check_free_copies(session, item, changes["quantity_available"], replacing=listing)
    for field, value in changes.items():
        setattr(listing, field, value)
    await session.flush()
    return listing


async def remove_listing(session: AsyncSession, current: CurrentUser, listing_id: uuid.UUID) -> None:
    """Soft delete: orders reference listings, so rows are kept with status=removed.
    Frees the copies the listing reserved."""
    listing = await _get_own_listing(session, current, listing_id)
    if listing.status == ListingStatus.removed:
        return
    if listing.status != ListingStatus.active:
        raise Conflict(f"Listing is {listing.status.value} and can't be removed")
    change_status(listing, ListingStatus.removed)
    await session.flush()
