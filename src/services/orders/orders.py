"""
Read access to orders. Orders are created by the checkout flow (Phase 4),
never directly from client input.
"""
import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth.auth import CurrentUser
from models.database import Order
from models.object_models.orders import OrderRole
from utils.errors import NotFound
from utils.pagination import paginate


async def get_my_orders(
    session: AsyncSession, current: CurrentUser, role: OrderRole, limit: int, cursor: str | None
) -> tuple[Sequence[Order], str | None]:
    party = Order.buyer_id if role == OrderRole.buyer else Order.seller_id
    stmt = select(Order).where(party == current.id).options(selectinload(Order.items))
    return await paginate(session, stmt, Order, limit, cursor)


async def get_order(session: AsyncSession, current: CurrentUser, order_id: uuid.UUID) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).options(selectinload(Order.items)))
    # Only the buyer and seller can see an order.
    if order is None or current.id not in (order.buyer_id, order.seller_id):
        raise NotFound("Order not found")
    return order
