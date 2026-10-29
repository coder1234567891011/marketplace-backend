import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.auth import CurrentUser
from models.database import User
from models.object_models.user import UserCreate, UserUpdate
from utils.errors import Conflict, Forbidden, NotFound


async def get_user(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    return user


async def require_profile(session: AsyncSession, current: CurrentUser) -> User:
    """The caller's profile row, which must exist before they can own anything."""
    user = await session.get(User, current.id)
    if user is None:
        raise Forbidden("Create a profile first (POST /v1/users/me)")
    return user


async def create_user(session: AsyncSession, current: CurrentUser, request: UserCreate) -> User:
    if await session.get(User, current.id) is not None:
        raise Conflict("Profile already exists")
    taken = await session.scalar(select(User.id).where(User.username == request.username))
    if taken is not None:
        raise Conflict("Username is taken")
    # Only client-editable fields are copied; ratings, sales and Stripe ids
    # start at their server defaults.
    user = User(id=current.id, **request.model_dump())
    session.add(user)
    await session.flush()
    return user


async def update_user(session: AsyncSession, current: CurrentUser, request: UserUpdate) -> User:
    user = await get_user(session, current.id)
    for field, value in request.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await session.flush()
    return user


async def delete_user(session: AsyncSession, current: CurrentUser) -> None:
    user = await get_user(session, current.id)
    await session.delete(user)
    # Users with listings, orders or ratings can't be deleted (FK violation → 409).
    await session.flush()
