import uuid

from models.object_models.orders import OrderRead
from services.persistence.database import SupabaseClient

client = SupabaseClient()
table = client.supabase_client.table('orders')

async def db_get_all_user_orders(user_id: uuid.UUID) -> list[OrderRead]:
    return [OrderRead()]

async def db_get_order(order_id: uuid.UUID) -> OrderRead:
    return OrderRead()

async def db_create_order(order : OrderRead) -> OrderRead:
    return OrderRead()

async def db_update_order(order_id: uuid.UUID, order: OrderRead) -> OrderRead:
    return OrderRead()

async def db_delete_order(order_id: uuid.UUID) -> None:
    return None