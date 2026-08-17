

import uuid
from models.object_models.addresses import AddressCreate, AddressRead
from services.persistence.database import SupabaseClient


client = SupabaseClient()
table = client.supabase_client.table('addresses')

async def db_get_user_addresses(user_id: uuid.UUID) -> list[AddressRead]:
    return [AddressRead]

async def db_get_address(address_id: uuid.UUID) -> AddressRead:
    return AddressRead

async def db_create_address(address: AddressCreate) -> AddressCreate:
    return AddressCreate

async def db_update_address(address: AddressCreate, address_id: uuid.UUID) -> AddressCreate:
    return AddressCreate

async def db_delete_address(address_id: uuid.UUID) -> None:
    return None