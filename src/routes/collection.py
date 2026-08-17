import uuid

from fastapi import Depends, APIRouter

from app import get_current_user
from models.object_models.collections import CollectionCreate, CollectionRead
from services.collections.collections import db_create_collection, db_delete_collection, db_get_collection, db_get_users_collection, db_update_collection


router = APIRouter(
    prefix="/collections",
    tags=["collections"],
)

@router.get("user/{user_id}")
async def get_user_collections(user_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> list[CollectionRead]:
    collections = await db_get_users_collection(user_id)
    return collections

@router.get("/{collection_id}")
async def get_collections(collection_id: uuid.UUID, id: uuid.UUID= Depends(get_current_user)) -> CollectionRead:
    collection = await db_get_collection(collection_id)
    return collection

@router.post("/")
async def create_collection(collection: CollectionCreate ,id: uuid.UUID = Depends(get_current_user)) -> CollectionCreate:
    collection = await db_create_collection(collection)

@router.put("/{collection_id}")
async def update_collection(collection: CollectionCreate,collection_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> CollectionCreate:
    collection = await db_update_collection(collection, collection_id)
    return collection

@router.delete("/{collection_id}")
async def delete_collection(collection_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> None:
    collection = db_delete_collection(collection_id)
    return None