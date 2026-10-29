import uuid

from fastapi import APIRouter

from auth.auth import AuthUser
from models.model import Page
from models.object_models.orders import OrderRead, OrderRole
from services.orders.orders import get_my_orders, get_order
from services.persistence.database import SessionDep
from utils.pagination import CursorParam, LimitParam

router = APIRouter(
    prefix="/orders",
    tags=["orders"],
)


@router.get("")
async def list_my_orders(
    current: AuthUser, session: SessionDep,
    role: OrderRole = OrderRole.buyer, limit: LimitParam = 50, cursor: CursorParam = None,
) -> Page[OrderRead]:
    orders, next_cursor = await get_my_orders(session, current, role, limit, cursor)
    return Page(items=[OrderRead.model_validate(o) for o in orders], next_cursor=next_cursor)


@router.get("/{order_id}")
async def read_order(order_id: uuid.UUID, current: AuthUser, session: SessionDep) -> OrderRead:
    return OrderRead.model_validate(await get_order(session, current, order_id))
