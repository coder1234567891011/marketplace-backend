# ----------------------------------------------------------------
# CART
# ----------------------------------------------------------------
from pydantic import BaseModel, Field

from models.object_models.listings import ListingRead
from models.object_models.shipping import ShippingRateRead
from models.object_models.user import UserRead


class CartItemSet(BaseModel):
    quantity: int = Field(ge=1, le=10_000)


class CartItemRead(BaseModel):
    listing: ListingRead
    quantity: int
    # Why this line can't be checked out as it is, e.g. "Only 2 available".
    problem: str | None = None


class CartSeller(BaseModel):
    """The cart's listings from one seller, which become one order."""
    seller: UserRead
    items: list[CartItemRead]
    subtotal_cents: int
    # The seller's shipping options, cheapest first; checkout picks one.
    shipping_rates: list[ShippingRateRead]


class CartRead(BaseModel):
    sellers: list[CartSeller]
    subtotal_cents: int
    item_count: int
    # False while any line has a problem or a seller has no shipping options.
    can_check_out: bool
