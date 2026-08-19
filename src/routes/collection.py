import uuid

from fastapi import Depends, APIRouter

from app import get_current_user
from models.object_models.collections import CollectionCreate, CollectionRead, CollectionItemRead, CollectionItemCreate
from services.collections.collections import db_create_collection, db_delete_collection, db_get_collection, \
    db_get_users_collection, db_update_collection, db_create_collection_items, db_get_collection_items, \
    db_get_collection_item, db_update_collection_item, db_delete_collection_item

router = APIRouter(
    prefix="/collections",
    tags=["collections"],
)

@router.get("/user/{user_id}")
async def get_user_collections(user_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> list[CollectionRead]:
    collections = await db_get_users_collection(user_id)
    return collections

@router.get("/{collection_id}")
async def get_collections(collection_id: uuid.UUID, id: uuid.UUID= Depends(get_current_user)) -> CollectionRead:
    collection = await db_get_collection(collection_id)
    return collection

@router.post("/")
async def create_collection(collection: CollectionCreate ,id: uuid.UUID = Depends(get_current_user)) -> CollectionCreate:
    collection = await db_create_collection(collection, id)
    return collection

@router.put("/{collection_id}")
async def update_collection(collection: CollectionCreate, collection_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> CollectionCreate:
    collection = await db_update_collection(collection, collection_id)
    return collection

@router.delete("/{collection_id}")
async def delete_collection(collection_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> None:
    collection = await db_delete_collection(collection_id)
    return None

@router.get("/{collection_id}/items")
async def get_all_collection_items(collection_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> list[CollectionItemRead]:
    collection_items = await db_get_collection_items(collection_id)
    return collection_items

@router.get("/items/{item_id}")
async def get_collection_item(item_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> CollectionItemRead:
    collection_item = await db_get_collection_item(item_id)
    return collection_item

@router.post("/{collection_id}/items")
async def create_items(collection_id: str, collection_items: list[CollectionItemCreate], id: uuid.UUID = Depends(get_current_user)) -> list[CollectionItemCreate]:
    create_new_collection_items = await db_create_collection_items(collection_items, collection_id)
    return create_new_collection_items

@router.put("/items/{item_id}")
async def update_item(item_id: uuid.UUID, collection: CollectionItemCreate ,id: uuid.UUID = Depends(get_current_user)) -> CollectionItemCreate:
    update_new_collection_item = await db_update_collection_item(collection, item_id)
    return update_new_collection_item

@router.delete("/items/{item_id}")
async def delete_item(item_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> None:
    delete_collection_item = await db_delete_collection_item(item_id)
    return None