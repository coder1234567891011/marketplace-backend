import uuid

from models.object_models.listings import ListingRead, ListingCreate
from services.persistence.database import SupabaseClient

client = SupabaseClient()
table = client.supabase_client.table('listings')

async def db_get_all_user_listings(user_id: uuid.UUID) -> list[ListingRead]:
    return [ListingRead()]

async def db_get_listing(listing_id: uuid.UUID) -> ListingRead:
    return ListingRead()

async def db_create_listing(listing : ListingCreate) -> ListingCreate:
    return ListingCreate()

async def db_update_listing(listing_id: uuid.UUID, listing: ListingCreate) -> ListingCreate:
    return ListingCreate()

async def db_delete_listing(listing_id: uuid.UUID) -> None:
    return None