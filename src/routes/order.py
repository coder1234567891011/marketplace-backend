import uuid

from fastapi import APIRouter

from auth.auth import AuthUser
from models.model import Page
from models.object_models.orders import OrderRead, OrderRole, OrderShip
from services.orders.orders import confirm_delivery, get_my_orders, get_order, refund_order, ship_order
from services.persistence.database import SessionDep
from utils import limits
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
    """Orders the caller bought, or (role=seller) paid orders they sold."""
    orders, next_cursor = await get_my_orders(session, current, role, limit, cursor)
    return Page(items=[OrderRead.model_validate(o) for o in orders], next_cursor=next_cursor)


@router.get("/{order_id}")
async def read_order(order_id: uuid.UUID, current: AuthUser, session: SessionDep) -> OrderRead:
    return OrderRead.model_validate(await get_order(session, current, order_id))


@router.post("/{order_id}/ship", dependencies=[limits.ORDER_ACTIONS])
async def mark_shipped(order_id: uuid.UUID, request: OrderShip, current: AuthUser, session: SessionDep) -> OrderRead:
    """Seller: the order is on its way. Pays the seller out, unless they were paid when the buyer paid."""
    return OrderRead.model_validate(await ship_order(session, current, order_id, request.tracking_number))


@router.post("/{order_id}/received", dependencies=[limits.ORDER_ACTIONS])
async def mark_received(order_id: uuid.UUID, current: AuthUser, session: SessionDep) -> OrderRead:
    """Buyer: the cards arrived. Both sides can then rate each other."""
    return OrderRead.model_validate(await confirm_delivery(session, current, order_id))


@router.post("/{order_id}/refund", dependencies=[limits.ORDER_ACTIONS])
async def refund(order_id: uuid.UUID, current: AuthUser, session: SessionDep) -> OrderRead:
    """Seller: cancel an unshipped order and refund the buyer in full."""
    return OrderRead.model_validate(await refund_order(session, current, order_id))
