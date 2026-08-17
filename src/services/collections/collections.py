
import uuid
from models.object_models.collections import CollectionCreate, CollectionRead
from services.persistence.database import SupabaseClient


client = SupabaseClient()
table = client.supabase_client.table('collections')

async def db_get_users_collection(user_id: uuid.UUID) -> list[CollectionRead]:
    return [CollectionRead]

async def db_get_collection(collection_id: uuid.UUID) -> CollectionRead:
    return CollectionRead

async def db_create_collection(collection: CollectionCreate) -> CollectionCreate:
    return CollectionCreate

async def db_update_collection(collection: CollectionCreate, collection_id: uuid.UUID) -> CollectionCreate:
    return CollectionCreate

async def db_delete_collection(collection_id: uuid.UUID) -> None:
    return None