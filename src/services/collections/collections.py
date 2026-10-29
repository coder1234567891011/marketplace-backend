import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth.auth import CurrentUser
from models.database import Collection, CollectionItem
from models.object_models.collections import (
    CollectionCreate,
    CollectionItemCreate,
    CollectionItemUpdate,
    CollectionUpdate,
)
from services.cards.cards import ensure_cards_exist
from services.user.user import require_profile
from utils.errors import NotFound
from utils.pagination import paginate


def _is_owner(collection: Collection, current: CurrentUser | None) -> bool:
    return current is not None and collection.user_id == current.id


async def get_user_collections(
    session: AsyncSession, current: CurrentUser | None, user_id: uuid.UUID
) -> Sequence[Collection]:
    stmt = select(Collection).where(Collection.user_id == user_id).order_by(Collection.created_at)
    if current is None or current.id != user_id:
        stmt = stmt.where(Collection.is_public.is_(True))
    return (await session.scalars(stmt)).all()


async def get_collection(
    session: AsyncSession, current: CurrentUser | None, collection_id: uuid.UUID
) -> Collection:
    """A collection the caller may view: their own, or anyone's public one."""
    collection = await session.get(Collection, collection_id)
    if collection is None or not (collection.is_public or _is_owner(collection, current)):
        raise NotFound("Collection not found")
    return collection


async def get_own_collection(session: AsyncSession, current: CurrentUser, collection_id: uuid.UUID) -> Collection:
    collection = await session.get(Collection, collection_id)
    if collection is None or not _is_owner(collection, current):
        raise NotFound("Collection not found")
    return collection


async def create_collection(session: AsyncSession, current: CurrentUser, request: CollectionCreate) -> Collection:
    await require_profile(session, current)
    collection = Collection(user_id=current.id, **request.model_dump())
    session.add(collection)
    await session.flush()
    return collection


async def update_collection(
    session: AsyncSession, current: CurrentUser, collection_id: uuid.UUID, request: CollectionUpdate
) -> Collection:
    collection = await get_own_collection(session, current, collection_id)
    for field, value in request.model_dump(exclude_unset=True).items():
        setattr(collection, field, value)
    await session.flush()
    return collection


async def delete_collection(session: AsyncSession, current: CurrentUser, collection_id: uuid.UUID) -> None:
    collection = await get_own_collection(session, current, collection_id)
    await session.delete(collection)
    await session.flush()


# ----------------------------------------------------------------
# COLLECTION ITEMS
# ----------------------------------------------------------------
async def get_collection_items(
    session: AsyncSession, current: CurrentUser | None, collection_id: uuid.UUID, limit: int, cursor: str | None
) -> tuple[Sequence[CollectionItem], str | None]:
    await get_collection(session, current, collection_id)
    stmt = (
        select(CollectionItem)
        .where(CollectionItem.collection_id == collection_id)
        .options(selectinload(CollectionItem.card))
    )
    return await paginate(session, stmt, CollectionItem, limit, cursor)


async def _load_item(session: AsyncSession, item_id: uuid.UUID) -> CollectionItem | None:
    stmt = (
        select(CollectionItem)
        .where(CollectionItem.id == item_id)
        .options(selectinload(CollectionItem.card), selectinload(CollectionItem.collection))
    )
    return await session.scalar(stmt)


async def get_collection_item(
    session: AsyncSession, current: CurrentUser | None, item_id: uuid.UUID
) -> CollectionItem:
    item = await _load_item(session, item_id)
    if item is None or not (item.collection.is_public or _is_owner(item.collection, current)):
        raise NotFound("Collection item not found")
    return item


async def get_own_collection_item(session: AsyncSession, current: CurrentUser, item_id: uuid.UUID) -> CollectionItem:
    item = await _load_item(session, item_id)
    if item is None or not _is_owner(item.collection, current):
        raise NotFound("Collection item not found")
    return item


async def create_collection_items(
    session: AsyncSession, current: CurrentUser, collection_id: uuid.UUID, requests: list[CollectionItemCreate]
) -> list[CollectionItem]:
    await get_own_collection(session, current, collection_id)
    await ensure_cards_exist(session, (r.card_id for r in requests))
    items = [CollectionItem(collection_id=collection_id, **r.model_dump()) for r in requests]
    session.add_all(items)
    await session.flush()
    ids = [i.id for i in items]
    stmt = select(CollectionItem).where(CollectionItem.id.in_(ids)).options(selectinload(CollectionItem.card))
    loaded = {i.id: i for i in (await session.scalars(stmt)).all()}
    return [loaded[i] for i in ids]


async def update_collection_item(
    session: AsyncSession, current: CurrentUser, item_id: uuid.UUID, request: CollectionItemUpdate
) -> CollectionItem:
    item = await get_own_collection_item(session, current, item_id)
    for field, value in request.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    await session.flush()
    return item


async def delete_collection_item(session: AsyncSession, current: CurrentUser, item_id: uuid.UUID) -> None:
    item = await get_own_collection_item(session, current, item_id)
    await session.delete(item)
    await session.flush()
