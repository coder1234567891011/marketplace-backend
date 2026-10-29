# ----------------------------------------------------------------
# ORDERS  (server-constructed at checkout, so there is no client Create)
# ----------------------------------------------------------------
import enum
import uuid
from datetime import datetime

from models.database import OrderStatus
from models.model import _ORMBase


class OrderItemRead(_ORMBase):
    listing_id: uuid.UUID
    quantity: int
    price_cents: int


class OrderRead(_ORMBase):
    id: uuid.UUID
    buyer_id: uuid.UUID
    seller_id: uuid.UUID
    subtotal_cents: int
    shipping_cents: int
    total_cents: int
    status: OrderStatus
    tracking_number: str | None
    items: list[OrderItemRead]
    updated_at: datetime
    created_at: datetime


class OrderRole(str, enum.Enum):
    """Which side of the order the current user is on, for listing their orders."""
    buyer = "buyer"
    seller = "seller"
