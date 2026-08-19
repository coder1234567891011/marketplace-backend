import uuid

from models.object_models.orders import OrderRead, OrderItemRead
from services.persistence.database import SupabaseClient

client = SupabaseClient()
table = client.supabase_client.table('orders')
items = client.supabase_client.table('order_items')

async def db_get_all_user_orders(user_id: uuid.UUID, role) -> list[OrderRead]:
    order_list = []
    orders = table.select("*").eq(f'{role}_id', user_id).execute().data
    print(orders)
    for order in orders:
        item_list = []
        new_items = items.select("*").eq('order_id', order['id']).execute().data
        for new_item in new_items:
            item_list.append(OrderItemRead(**new_item))
        order_list.append(OrderRead(items=item_list,**order))

    return order_list

async def db_get_order(order_id: uuid.UUID) -> OrderRead:
    order = table.select("*").eq("id", order_id).execute().data[0]
    order_items = items.select("*").eq("order_id", order['id']).execute().data
    item_list = []
    for item in order_items:
        item_list.append(OrderItemRead(**item))
    return OrderRead(items = item_list,**order)

async def db_create_order(order : OrderRead) -> OrderRead:
    payload = order.model_dump(exclude={"items"}, exclude_none=True)
    payload['buyer_id'] = str(payload['buyer_id'])
    payload['seller_id'] = str(payload['seller_id'])
    new_order = table.insert(payload).execute().data[0]
    new_order_id = new_order['id']
    item_list=[]
    for item in order.items:
        item_payload = item.model_dump(mode="json", exclude_none=True)
        item_payload['order_id'] = str(new_order_id)
        item_payload['listing_id'] = str(item_payload['listing_id'])
        new_item = items.insert(item_payload).execute().data[0]
        item_list.append(OrderItemRead(**new_item))

    return OrderRead(id=new_order['id'],
                     buyer_id=new_order['buyer_id'],
                     seller_id=new_order['seller_id'],
                     subtotal_cents=new_order['subtotal_cents'],
                     shipping_cents=new_order['shipping_cents'],
                     total_cents=new_order['total_cents'],
                     status=new_order['status'],
                     tracking_number=new_order['tracking_number'],
                     items=item_list,
                     created_at=new_order['created_at']
                     )

async def db_update_order(order_id: uuid.UUID, order: OrderRead) -> OrderRead:
    payload = order.model_dump(exclude={"items"}, exclude_none=True)
    payload['id'] = str(order_id)
    payload['buyer_id'] = str(payload['buyer_id'])
    payload['seller_id'] = str(payload['seller_id'])
    new_order = table.update(payload).eq("id", order_id).execute().data[0]
    item_list = []
    for item in order.items:
        item_payload = item.model_dump(mode="json", exclude_none=True)
        item_payload['order_id'] = str(order_id)
        item_payload['listing_id'] = str(item_payload['listing_id'])
        new_item = items.update(item_payload).eq('listing_id',item_payload['listing_id']).execute().data[0]
        item_list.append(OrderItemRead(**new_item))

    return OrderRead(id=new_order['id'],
                     buyer_id=new_order['buyer_id'],
                     seller_id=new_order['seller_id'],
                     subtotal_cents=new_order['subtotal_cents'],
                     shipping_cents=new_order['shipping_cents'],
                     total_cents=new_order['total_cents'],
                     status=new_order['status'],
                     tracking_number=new_order['tracking_number'],
                     items=item_list,
                     created_at=new_order['created_at']
                     )

async def db_delete_order(order_id: uuid.UUID) -> None:
    delete_items = items.delete().eq("order_id", str(order_id)).execute()
    delete_order = table.delete().eq("id", str(order_id)).execute()
    return None