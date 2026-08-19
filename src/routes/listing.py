import uuid

from fastapi import APIRouter, Depends

from app import get_current_user
from models.object_models.listings import ListingRead, ListingCreate
from services.listings.listings import db_get_all_user_listings, db_get_listing, db_create_listing, db_update_listing, \
    db_delete_listing

router = APIRouter(
    prefix="/listings",
    tags=["listings"],
)

@router.get("/user/{user_id}")
async def get_user_listings(user_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> list[ListingRead]:
    listings = await db_get_all_user_listings(user_id)
    return listings


@router.get("/{listing_id}")
async def get_listings(listing_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> ListingRead:
    listing = await db_get_listing(listing_id)
    return listing

@router.post("/")
async def create_listing(listing: ListingCreate, id: uuid.UUID = Depends(get_current_user)) -> ListingCreate:
    new_listing = await db_create_listing(listing, id["sub"])
    return new_listing

@router.put("/{listing_id}")
async def update_listing(listing: ListingCreate , listing_id: uuid.UUID ,id: uuid.UUID = Depends(get_current_user)) -> ListingCreate:
    updated_listing = await db_update_listing(listing_id, listing, id["sub"])
    return updated_listing

@router.delete("/{listing_id}")
async def delete_listing(listing_id: uuid.UUID, id: uuid.UUID = Depends(get_current_user)) -> None:
    listing = await db_delete_listing(listing_id)
    return None