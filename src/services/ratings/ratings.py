import uuid

from models.object_models.ratings import RatingCreate, RatingRead
from services.persistence.database import SupabaseClient

client = SupabaseClient()
table = client.supabase_client.table("ratings")

async def db_get_user_ratings(user_id: uuid.UUID) -> list[RatingRead]:
    ratings_list = []
    ratings = table.select("*").eq("user_id", user_id).execute()
    for rating in ratings.data:
        ratings_list.append(RatingRead(
            **rating.data[0]
        ))
    return ratings_list

async def db_get_rating(rating_id: uuid.UUID) -> RatingRead:
    rating = table.select("*").eq("rating_id", rating_id).execute()
    return RatingRead(
        **rating.data[0]
    )

async def db_create_rating(rating: RatingCreate) -> RatingCreate:
    created_rating = table.insert(rating.model_dump()).execute()
    return RatingCreate(
        **created_rating.data[0]
    )

async def db_delete_rating(rating_id: uuid.UUID) -> None:
    delete_rating = table.delete().eq("id", rating_id).execute()
    return None