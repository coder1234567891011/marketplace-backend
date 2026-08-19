# ----------------------------------------------------------------
# ORDERS  (server-constructed at checkout, not a raw client Create)
# ----------------------------------------------------------------
import uuid
from datetime import datetime

from models.database import OrderStatus
from models.model import _ORMBase


class OrderItemRead(_ORMBase):
    listing_id: uuid.UUID
    quantity: int
    price_cents: int


class OrderRead(_ORMBase):
    id: uuid.UUID | None
    buyer_id: uuid.UUID
    seller_id: uuid.UUID
    subtotal_cents: int
    shipping_cents: int
    total_cents: int
    status: OrderStatus
    tracking_number: str | None
    items: list[OrderItemRead]
    created_at: datetime | None