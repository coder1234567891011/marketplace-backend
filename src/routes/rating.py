import uuid

from fastapi import APIRouter, Depends

from app import get_current_user
from models.object_models.ratings import RatingCreate, RatingRead
from services.ratings.ratings import db_create_rating, db_get_rating, db_get_user_ratings, db_delete_rating

router = APIRouter(
    prefix="/ratings",
    tags=["ratings"],
)

@router.get("/{user_id}")
async def get_user_ratings(user_id: uuid.UUID , id: uuid.UUID = Depends(get_current_user)) -> list[RatingRead]:
    user_ratings = await db_get_user_ratings(user_id)
    return user_ratings

@router.get("/{rating_id}")
async def get_rating(rating_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> RatingRead:
    rating = await db_get_rating(rating_id)
    return rating

@router.post("/", response_model=RatingCreate)
async def create_rating(rating: RatingCreate, user_id: uuid.UUID = Depends(get_current_user)) -> RatingCreate:
    new_rating = await db_create_rating(rating, user_id)
    return new_rating

@router.delete("/{rating_id}")
async def delete_rating(rating_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> None:
    rating = await db_delete_rating(rating_id)
    return None