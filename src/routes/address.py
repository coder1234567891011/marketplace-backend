import uuid

from fastapi import APIRouter, status

from auth.auth import AuthUser
from models.object_models.addresses import AddressCreate, AddressRead
from services.addresses.addresses import (
    create_address,
    delete_address,
    get_address,
    get_my_addresses,
    update_address,
)
from services.persistence.database import SessionDep
from utils import limits

router = APIRouter(
    prefix="/addresses",
    tags=["addresses"],
)


@router.get("")
async def list_my_addresses(current: AuthUser, session: SessionDep) -> list[AddressRead]:
    return [AddressRead.model_validate(a) for a in await get_my_addresses(session, current)]


@router.get("/{address_id}")
async def read_address(address_id: uuid.UUID, current: AuthUser, session: SessionDep) -> AddressRead:
    return AddressRead.model_validate(await get_address(session, current, address_id))


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[limits.ADDRESSES])
async def add_address(request: AddressCreate, current: AuthUser, session: SessionDep) -> AddressRead:
    return AddressRead.model_validate(await create_address(session, current, request))


@router.put("/{address_id}", dependencies=[limits.ADDRESSES])
async def replace_address(
    address_id: uuid.UUID, request: AddressCreate, current: AuthUser, session: SessionDep
) -> AddressRead:
    return AddressRead.model_validate(await update_address(session, current, address_id, request))


@router.delete("/{address_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[limits.ADDRESSES])
async def remove_address(address_id: uuid.UUID, current: AuthUser, session: SessionDep) -> None:
    await delete_address(session, current, address_id)
