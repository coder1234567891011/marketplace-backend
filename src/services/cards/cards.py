import uuid

from models.database import Card
from models.object_models.cards import CardRead
from services.persistence.database import SupabaseClient

client = SupabaseClient()
table = client.supabase_client.table('cards')

async def db_get_card(card_id : uuid.UUID):
    card = table.select("*",card_id["sub"]).execute()
    return CardRead(**card.data[0])

async def db_create_card(card:Card):
    card = table.insert(card).execute()
    return CardRead(**card.data[0])