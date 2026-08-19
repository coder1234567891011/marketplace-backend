import uuid
from typing import Any

from fastapi import Depends, APIRouter

from auth.auth import get_current_user
from models.object_models.user import UserRead, UserUpdate
from services.user.user import db_delete_user, db_create_user, db_get_user, db_update_user

router = APIRouter(
    prefix="/users",
    tags=["user"],
)

@router.get("/{user_id}", response_model=UserRead)
async def read_user(user_id: uuid.UUID, req_id : uuid.UUID = Depends(get_current_user)) -> UserRead | None:
    if str(user_id) == str(req_id["sub"]):
        user = await db_get_user(user_id)
        user_read = UserRead(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            bio=user.bio,
            account_status=user.account_status,
            seller_rating_avg=user.seller_rating_avg,
            seller_rating_count=user.seller_rating_count,
            buyer_rating_avg=user.buyer_rating_avg,
            buyer_rating_count=user.buyer_rating_count,
            total_sales=user.total_sales,
            created_at=user.created_at,
        )
        return user_read
    else:
        return None

@router.post("/", response_model=UserRead)
async def create_new_user(request: UserRead, user_id: uuid.UUID = Depends(get_current_user)) -> UserRead:
    user = await db_create_user(user_id, request)
    user_read = UserRead(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        bio=user.bio,
        account_status=user.account_status,
        seller_rating_avg=user.seller_rating_avg,
        seller_rating_count=user.seller_rating_count,
        buyer_rating_avg=user.buyer_rating_avg,
        buyer_rating_count=user.buyer_rating_count,
        total_sales=user.total_sales,
        created_at=user.created_at,
    )
    return user_read

@router.put("/{user_id}", response_model=UserRead)
async def update_user(request : UserUpdate, user_id: uuid.UUID = Depends(get_current_user)) -> UserRead:
    user = await db_update_user(user_id, request)
    user_read = UserRead(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        bio=user.bio,
        account_status=user.account_status,
        seller_rating_avg=user.seller_rating_avg,
        seller_rating_count=user.seller_rating_count,
        buyer_rating_avg=user.buyer_rating_avg,
        buyer_rating_count=user.buyer_rating_count,
        total_sales=user.total_sales,
        created_at=user.created_at,
    )
    return user_read

@router.delete("/{user_id}", response_model=None)
async def delete_user(user_id: uuid.UUID = Depends(get_current_user)) -> None:
    await db_delete_user(user_id["sub"])
    return None