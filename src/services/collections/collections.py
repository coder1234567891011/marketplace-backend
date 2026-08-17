
import uuid

from models.database import CollectionItem
from models.object_models.collections import CollectionCreate, CollectionRead, CollectionItemCreate, CollectionItemRead
from services.persistence.database import SupabaseClient


client = SupabaseClient()
table = client.supabase_client.table('collections')
items = client.supabase_client.table('collection_items')
cards = client.supabase_client.table('cards')

async def db_get_users_collection(user_id: uuid.UUID) -> list[CollectionRead]:
    collections_list = []
    collections = table.select("*").eq("user_id", user_id).execute()
    for collection in collections.data:
        collections_list.append(CollectionRead(
            **collection
        ))
    return collections_list

async def db_get_collection(collection_id: uuid.UUID) -> CollectionRead:
    collection = table.select("*").eq("id", collection_id).execute()
    return CollectionRead(**collection.data[0])

async def db_create_collection(collection: CollectionCreate, id: uuid.UUID) -> CollectionCreate:
    payload = collection.model_dump()
    payload['user_id'] = id["sub"]
    new_collection = table.insert(payload).execute()
    return CollectionCreate(**new_collection.data[0])

async def db_update_collection(collection: CollectionCreate, collection_id: uuid.UUID) -> CollectionCreate:
    update_collection = table.update(collection.model_dump()).eq("id", collection_id).execute()
    return CollectionCreate(**update_collection.data[0])

async def db_delete_collection(collection_id: uuid.UUID) -> None:
    delete_collection = table.delete().eq("id", collection_id).execute()
    return None

async def db_get_collection_items(collection_id: uuid.UUID) -> list[CollectionItemRead]:
    collection_list : list[CollectionItemRead] = []
    collection_item = table.select("*").eq("id", collection_id).execute()
    for item in collection_item.data:
        print(item)
        collection_list.append(CollectionItemRead(**item))
    return collection_list

async def db_create_collection_items(collection: list[CollectionItemCreate], collection_id: str) -> list[CollectionItemCreate]:
    collection_list : list[CollectionItemCreate] = []
    for item in collection:
        payload = item.model_dump()
        payload["collection_id"] = collection_id
        payload["card_id"] = str(payload["card_id"])
        new_collection_item = items.insert(payload).execute()
        collection_list.append(new_collection_item.data[0])
    return collection_list