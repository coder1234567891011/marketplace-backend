import uuid
from collections.abc import Sequence

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from auth.auth import CurrentUser
from models.database import Address
from models.object_models.addresses import AddressCreate
from services.user.user import require_profile
from utils.errors import NotFound


async def get_my_addresses(session: AsyncSession, current: CurrentUser) -> Sequence[Address]:
    stmt = select(Address).where(Address.user_id == current.id).order_by(Address.created_at)
    return (await session.scalars(stmt)).all()


async def get_address(session: AsyncSession, current: CurrentUser, address_id: uuid.UUID) -> Address:
    address = await session.get(Address, address_id)
    # Addresses are private: someone else's address is reported as missing.
    if address is None or address.user_id != current.id:
        raise NotFound("Address not found")
    return address


async def _clear_default(session: AsyncSession, user_id: uuid.UUID) -> None:
    await session.execute(
        update(Address).where(Address.user_id == user_id, Address.is_default.is_(True)).values(is_default=False)
    )


async def create_address(session: AsyncSession, current: CurrentUser, request: AddressCreate) -> Address:
    await require_profile(session, current)
    if request.is_default:
        await _clear_default(session, current.id)
    address = Address(user_id=current.id, **request.model_dump())
    session.add(address)
    await session.flush()
    return address


async def update_address(
    session: AsyncSession, current: CurrentUser, address_id: uuid.UUID, request: AddressCreate
) -> Address:
    address = await get_address(session, current, address_id)
    if request.is_default and not address.is_default:
        await _clear_default(session, current.id)
    for field, value in request.model_dump().items():
        setattr(address, field, value)
    await session.flush()
    return address


async def delete_address(session: AsyncSession, current: CurrentUser, address_id: uuid.UUID) -> None:
    address = await get_address(session, current, address_id)
    await session.delete(address)
    await session.flush()
