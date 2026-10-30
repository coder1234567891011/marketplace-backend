import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from models.database import CardCondition, CardFinish
from models.model import _ORMBase
from models.object_models.cards import CardRead
from models.object_models.collections import ImportProblem, Language
from models.object_models.listings import ListingRead
from models.object_models.user import UserRead


# ----------------------------------------------------------------
# LISTS AND ITEMS
# ----------------------------------------------------------------
class WantsListCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class WantsListRead(_ORMBase):
    id: uuid.UUID
    name: str
    item_count: int = 0
    created_at: datetime
    updated_at: datetime


class WantItemCreate(BaseModel):
    card_id: uuid.UUID
    # True: any printing of the card will do (card_id is just an example).
    any_printing: bool = True
    quantity: int = Field(default=1, ge=1, le=10_000)
    # The worst acceptable condition; null for any.
    min_condition: CardCondition | None = None
    finish: CardFinish | None = None
    language: Language | None = None
    max_price_cents: int | None = Field(default=None, ge=1)


class WantItemUpdate(BaseModel):
    """Only the fields sent change; send null to clear min_condition, finish,
    language or max_price_cents."""
    any_printing: bool | None = None
    quantity: int | None = Field(default=None, ge=1, le=10_000)
    min_condition: CardCondition | None = None
    finish: CardFinish | None = None
    language: Language | None = None
    max_price_cents: int | None = Field(default=None, ge=1)


class WantItemRead(_ORMBase):
    id: uuid.UUID
    card: CardRead
    any_printing: bool
    quantity: int
    min_condition: CardCondition | None
    finish: CardFinish | None
    language: str | None
    max_price_cents: int | None
    created_at: datetime


class WantItemStatus(WantItemRead):
    """An item with what the caller owns and what's for sale."""
    # Copies in the caller's collections (any condition).
    owned: int
    # Copies for sale that match, and the cheapest of them.
    available: int
    lowest_price_cents: int | None


class WantsImport(BaseModel):
    """A pasted decklist (Arena, MTGO or plain "4 Lightning Bolt" lines). Lines
    with a set and number want that printing; others want any printing."""
    text: str = Field(min_length=1, max_length=200_000)
    min_condition: CardCondition | None = None


class WantsImportResult(BaseModel):
    # Items added, and existing items whose quantity went up.
    added: int
    updated: int
    unmatched: list[ImportProblem]


# ----------------------------------------------------------------
# OPTIMIZER
# ----------------------------------------------------------------
class OptimizeRequest(BaseModel):
    # Only buy what the caller's collections don't already have.
    skip_owned: bool = True
    # Put the plan in the cart (replacing the quantities of those listings already in it).
    add_to_cart: bool = False


class PlannedLine(BaseModel):
    want_item_id: uuid.UUID
    listing: ListingRead
    quantity: int


class PlannedSeller(BaseModel):
    seller: UserRead
    lines: list[PlannedLine]
    subtotal_cents: int
    # The seller's cheapest shipping rate, which checkout uses by default.
    shipping_rate_id: uuid.UUID
    shipping_cents: int


class MissingItem(BaseModel):
    want_item_id: uuid.UUID
    card: CardRead
    # Copies nobody is selling (that match the item).
    quantity: int


class OptimizeResult(BaseModel):
    sellers: list[PlannedSeller]
    subtotal_cents: int
    shipping_cents: int
    total_cents: int
    missing: list[MissingItem]
    added_to_cart: bool
