import uuid

from fastapi import Depends, APIRouter

from app import get_current_user
from models.object_models.addresses import AddressCreate, AddressRead
from services.addresses.addresses import db_create_address, db_delete_address, db_get_address, db_get_user_addresses, db_update_address

router = APIRouter(
    prefix="/addresses",
    tags=["addresses"],
)

@router.get("/user/{user_id}")
async def get_user_addresses(user_id: uuid.UUID,id: uuid.UUID = Depends(get_current_user)) -> list[AddressRead]:
    addresses = await db_get_user_addresses(user_id)
    return addresses

@router.get("/{address_id}")
async def get_address(address_id: uuid.UUID,id: uuid.UUID = Depends(get_current_user)) -> AddressRead:
    address = await db_get_address(address_id)
    return address

@router.post("/")
async def create_address(address: AddressCreate, user_id: uuid.UUID = Depends(get_current_user)) -> AddressCreate:
    new_address = await db_create_address(address, user_id)
    return new_address

@router.put("/{address_id}")
async def update_address(address: AddressCreate, address_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> AddressCreate:
    updated_address = await db_update_address(address, address_id)
    return updated_address

@router.delete("/{address_id}")
async def delete_address(address_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> None:
    address = await db_delete_address(address_id)
    return None
