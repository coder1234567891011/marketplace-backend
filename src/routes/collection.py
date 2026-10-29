import uuid
from typing import Annotated

from fastapi import APIRouter, Body, status

from auth.auth import AuthUser, OptionalUser
from models.model import Page
from models.object_models.collections import (
    CollectionCreate,
    CollectionItemCreate,
    CollectionItemRead,
    CollectionItemUpdate,
    CollectionRead,
    CollectionUpdate,
)
from services.collections.collections import (
    create_collection,
    create_collection_items,
    delete_collection,
    delete_collection_item,
    get_collection,
    get_collection_item,
    get_collection_items,
    get_user_collections,
    update_collection,
    update_collection_item,
)
from services.persistence.database import SessionDep
from utils import limits
from utils.pagination import CursorParam, LimitParam

router = APIRouter(
    prefix="/collections",
    tags=["collections"],
)


@router.get("/user/{user_id}")
async def list_user_collections(
    user_id: uuid.UUID, current: OptionalUser, session: SessionDep
) -> list[CollectionRead]:
    return [CollectionRead.model_validate(c) for c in await get_user_collections(session, current, user_id)]


@router.get("/{collection_id}")
async def read_collection(collection_id: uuid.UUID, current: OptionalUser, session: SessionDep) -> CollectionRead:
    return CollectionRead.model_validate(await get_collection(session, current, collection_id))


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[limits.COLLECTIONS])
async def add_collection(request: CollectionCreate, current: AuthUser, session: SessionDep) -> CollectionRead:
    return CollectionRead.model_validate(await create_collection(session, current, request))


@router.patch("/{collection_id}", dependencies=[limits.COLLECTIONS])
async def edit_collection(
    collection_id: uuid.UUID, request: CollectionUpdate, current: AuthUser, session: SessionDep
) -> CollectionRead:
    return CollectionRead.model_validate(await update_collection(session, current, collection_id, request))


@router.delete("/{collection_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[limits.COLLECTIONS])
async def remove_collection(collection_id: uuid.UUID, current: AuthUser, session: SessionDep) -> None:
    await delete_collection(session, current, collection_id)


@router.get("/{collection_id}/items")
async def list_collection_items(
    collection_id: uuid.UUID, current: OptionalUser, session: SessionDep,
    limit: LimitParam = 50, cursor: CursorParam = None,
) -> Page[CollectionItemRead]:
    items, next_cursor = await get_collection_items(session, current, collection_id, limit, cursor)
    return Page(items=[CollectionItemRead.model_validate(i) for i in items], next_cursor=next_cursor)


@router.post("/{collection_id}/items", status_code=status.HTTP_201_CREATED, dependencies=[limits.COLLECTION_ITEMS])
async def add_collection_items(
    collection_id: uuid.UUID,
    requests: Annotated[list[CollectionItemCreate], Body(min_length=1, max_length=500)],
    current: AuthUser, session: SessionDep,
) -> list[CollectionItemRead]:
    items = await create_collection_items(session, current, collection_id, requests)
    return [CollectionItemRead.model_validate(i) for i in items]


@router.get("/items/{item_id}")
async def read_collection_item(item_id: uuid.UUID, current: OptionalUser, session: SessionDep) -> CollectionItemRead:
    return CollectionItemRead.model_validate(await get_collection_item(session, current, item_id))


@router.patch("/items/{item_id}", dependencies=[limits.COLLECTION_ITEMS])
async def edit_collection_item(
    item_id: uuid.UUID, request: CollectionItemUpdate, current: AuthUser, session: SessionDep
) -> CollectionItemRead:
    return CollectionItemRead.model_validate(await update_collection_item(session, current, item_id, request))


@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[limits.COLLECTION_ITEMS])
async def remove_collection_item(item_id: uuid.UUID, current: AuthUser, session: SessionDep) -> None:
    await delete_collection_item(session, current, item_id)
