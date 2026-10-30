# ----------------------------------------------------------------
# ORDERS  (server-constructed at checkout, so there is no client Create)
# ----------------------------------------------------------------
import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from models.database import CardCondition, CardFinish, CheckoutStatus, OrderStatus
from models.model import _ORMBase
from models.object_models.cards import CardRead


class OrderItemRead(_ORMBase):
    listing_id: uuid.UUID
    card: CardRead
    condition: CardCondition
    finish: CardFinish
    language: str
    quantity: int
    # Price of one copy when it was bought.
    price_cents: int


class OrderRead(_ORMBase):
    id: uuid.UUID
    checkout_id: uuid.UUID | None
    buyer_id: uuid.UUID
    seller_id: uuid.UUID
    subtotal_cents: int
    shipping_cents: int
    total_cents: int
    currency: str
    # The platform's fee and what the seller receives (total minus fee).
    platform_fee_cents: int
    seller_payout_cents: int
    status: OrderStatus
    shipping_rate_name: str | None
    shipping_address: dict[str, Any] | None
    tracking_number: str | None
    items: list[OrderItemRead]
    paid_at: datetime | None
    shipped_at: datetime | None
    delivered_at: datetime | None
    updated_at: datetime
    created_at: datetime


class OrderRole(str, enum.Enum):
    """Which side of the order the current user is on, for listing their orders."""
    buyer = "buyer"
    seller = "seller"


class OrderShip(BaseModel):
    tracking_number: str | None = Field(default=None, max_length=100)


# ----------------------------------------------------------------
# CHECKOUT
# ----------------------------------------------------------------
class CheckoutCreate(BaseModel):
    shipping_address_id: uuid.UUID
    # Shipping rate per seller (seller id → rate id). Sellers left out get
    # their cheapest rate.
    shipping_rates: dict[uuid.UUID, uuid.UUID] = {}


class CheckoutRead(_ORMBase):
    id: uuid.UUID
    status: CheckoutStatus
    total_cents: int
    currency: str
    # Stripe's payment page: send the buyer here. Only set while open.
    url: str | None = Field(default=None, validation_alias="stripe_checkout_url")
    # The copies are held until then.
    expires_at: datetime
    paid_at: datetime | None
    orders: list[OrderRead]
    created_at: datetime
