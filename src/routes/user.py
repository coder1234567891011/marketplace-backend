import uuid

from fastapi import APIRouter, status

from auth.auth import AuthUser
from models.object_models.user import UserCreate, UserRead, UserUpdate
from services.persistence.database import SessionDep
from services.user.user import create_user, delete_user, get_user, update_user
from utils import limits

router = APIRouter(
    prefix="/users",
    tags=["users"],
)


@router.get("/me")
async def read_me(current: AuthUser, session: SessionDep) -> UserRead:
    return UserRead.model_validate(await get_user(session, current.id))


@router.post("/me", status_code=status.HTTP_201_CREATED, dependencies=[limits.PROFILE_CREATE])
async def create_me(request: UserCreate, current: AuthUser, session: SessionDep) -> UserRead:
    return UserRead.model_validate(await create_user(session, current, request))


@router.patch("/me", dependencies=[limits.PROFILE_UPDATE])
async def update_me(request: UserUpdate, current: AuthUser, session: SessionDep) -> UserRead:
    return UserRead.model_validate(await update_user(session, current, request))


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(current: AuthUser, session: SessionDep) -> None:
    await delete_user(session, current)


@router.get("/{user_id}")
async def read_user(user_id: uuid.UUID, session: SessionDep) -> UserRead:
    return UserRead.model_validate(await get_user(session, user_id))
