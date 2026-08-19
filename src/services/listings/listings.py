import uuid

from models.object_models.listings import ListingRead, ListingCreate
from services.persistence.database import SupabaseClient

client = SupabaseClient()
table = client.supabase_client.table('listings')
card = client.supabase_client.table('cards')
user = client.supabase_client.table('users')

async def db_get_all_user_listings(user_id: uuid.UUID) -> list[ListingRead]:
    listing_list = []
    listings = table.select("*").eq('seller_id', user_id).execute()
    for listing in listings.data:
        find_seller = user.select("*").eq("id",listing["seller_id"]).execute()
        find_card = card.select("*").eq("id", listing["card_id"]).execute()
        listing_list.append(ListingRead(seller=find_seller.data[0], card=find_card.data[0], **listing))
    return listing_list

async def db_get_listing(listing_id: uuid.UUID) -> ListingRead:
    listing = table.select("*").eq("id", listing_id).execute().data[0]
    find_seller = user.select("*").eq("id", listing["seller_id"]).execute()
    find_card = card.select("*").eq('id', listing["card_id"]).execute()
    return ListingRead(seller=find_seller.data[0], card=find_card.data[0], **listing)

async def db_create_listing(listing : ListingCreate, id: uuid.UUID) -> ListingCreate:
    payload = listing.model_dump()
    payload['seller_id'] = id
    payload["card_id"]=str(payload["card_id"])
    payload["collection_item_id"]=str(payload["collection_item_id"])
    new_listing = table.insert(payload).execute().data[0]
    return ListingCreate(card_id=new_listing["card_id"],
                         collection_item_id=new_listing["collection_item_id"],
                         condition=new_listing["condition"],
                         is_foil=new_listing["is_foil"],
                         quantity_available=new_listing["quantity_available"],
                         price_cents=new_listing["price_cents"],)

async def db_update_listing(listing_id: uuid.UUID, listing: ListingCreate, id) -> ListingCreate:
    payload = listing.model_dump()
    payload['seller_id'] = id
    payload["card_id"]=str(payload["card_id"])
    payload["collection_item_id"]=str(payload["collection_item_id"])
    new_listing = table.update(payload).eq('id',listing_id).execute().data[0]
    return ListingCreate(
        card_id=new_listing["card_id"],
        collection_item_id=new_listing["collection_item_id"],
        condition=new_listing["condition"],
        is_foil=new_listing["is_foil"],
        quantity_available=new_listing["quantity_available"],
        price_cents=new_listing["price_cents"]
    )

async def db_delete_listing(listing_id: uuid.UUID) -> None:
    delete_listing=table.delete().eq('id', listing_id).execute().data[0]
    return None