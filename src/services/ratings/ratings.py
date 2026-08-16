import uuid

from models.object_models.ratings import RatingCreate, RatingRead
from services.persistence.database import SupabaseClient

client = SupabaseClient()
table = client.supabase_client.table("ratings")

async def db_get_user_ratings(user_id: uuid.UUID) -> list[RatingRead]:
    return [RatingRead()]

async def db_get_rating(rating_id: uuid.UUID) -> RatingRead:
    return RatingRead()

async def db_create_rating(rating: RatingCreate, user_id: uuid.UUID) -> RatingCreate:
    return RatingCreate()

async def db_delete_rating(rating_id: uuid.UUID) -> None:
    return None