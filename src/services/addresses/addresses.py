

import uuid
from models.object_models.addresses import AddressCreate, AddressRead
from services.persistence.database import SupabaseClient


client = SupabaseClient()
table = client.supabase_client.table('addresses')

async def db_get_user_addresses(user_id: uuid.UUID) -> list[AddressRead]:
    address_list = []
    addresses = table.select("*").eq("user_id", user_id).execute()
    for address in addresses.data:
        address_list.append(AddressRead(**address))
    return address_list

async def db_get_address(address_id: uuid.UUID) -> AddressRead:
    address = table.select("*").eq("id", address_id).execute()
    return AddressRead(**address.data[0])

async def db_create_address(address: AddressCreate, user_id: uuid.UUID) -> AddressCreate:
    payload = address.model_dump()
    payload["user_id"] = user_id["sub"]
    new_address = table.insert(payload).execute()
    return AddressCreate(**new_address.data[0])

async def db_update_address(address: AddressCreate, address_id: uuid.UUID) -> AddressCreate:
    address = table.update(address.model_dump()).eq("id", address_id).execute()
    return AddressCreate(**address.data[0])

async def db_delete_address(address_id: uuid.UUID) -> None:
    address = table.delete().eq("id", address_id).execute()
    return None