import uuid

from fastapi import APIRouter, Depends

from app import get_current_user
from models.object_models.orders import OrderRead
from services.orders.orders import db_get_order, db_create_order, db_get_all_user_orders, db_update_order, \
    db_delete_order

router = APIRouter(
    prefix="/orders",
    tags=["order"],
)

@router.get("/user/{user_id}/{role}")
async def get_user_orders(user_id : uuid.UUID, role: str,  id: uuid.UUID = Depends(get_current_user)) -> list[OrderRead]:
    orders = await db_get_all_user_orders(user_id, role)
    return orders

@router.get("/{order_id}")
async def get_order(order_id: uuid.UUID, user_id: uuid.UUID = Depends(get_current_user)) -> OrderRead:
    order = await db_get_order(order_id)
    return order

@router.post("/")
async def create_order(order:OrderRead, user_id: uuid.UUID = Depends(get_current_user)) -> OrderRead:
    order = await db_create_order(order)
    return order

@router.put("/{order_id}")
async def update_order(order_id: uuid.UUID, updated_order: OrderRead, user_id: uuid.UUID = Depends(get_current_user)) -> OrderRead:
    order = await db_update_order(order_id, updated_order)
    return order

@router.delete("/{order_id}")
async def delete_order(order_id: uuid.UUID, user_id: uuid.UUID = Depends(get_current_user)) -> None:
    order = await db_delete_order(order_id)
    return None