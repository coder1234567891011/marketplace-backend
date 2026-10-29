# ----------------------------------------------------------------
# LISTINGS
# ----------------------------------------------------------------
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from models.database import CardCondition, CardFinish, ListingStatus
from models.model import _ORMBase
from models.object_models.cards import CardRead
from models.object_models.collections import Language
from models.object_models.user import UserRead


class ListingCreate(BaseModel):
    card_id: uuid.UUID
    # Listing from a collection item reserves that many of its copies, and
    # finish/language default to the item's.
    collection_item_id: uuid.UUID | None = None
    condition: CardCondition
    finish: CardFinish | None = None
    language: Language | None = None
    quantity_available: int = Field(default=1, ge=1, le=10_000)
    price_cents: int = Field(gt=0, le=10_000_000)
    notes: str | None = Field(default=None, max_length=500)


class ListingUpdate(BaseModel):
    condition: CardCondition | None = None
    quantity_available: int | None = Field(default=None, ge=1, le=10_000)
    price_cents: int | None = Field(default=None, gt=0, le=10_000_000)
    notes: str | None = Field(default=None, max_length=500)


class ListingRead(_ORMBase):
    id: uuid.UUID
    collection_item_id: uuid.UUID | None
    seller: UserRead
    card: CardRead
    condition: CardCondition
    finish: CardFinish
    language: str
    quantity_available: int
    price_cents: int
    currency: str
    status: ListingStatus
    notes: str | None
    created_at: datetime
    updated_at: datetime


class ListingGroup(BaseModel):
    """Active listings of one card in one condition and finish."""
    condition: CardCondition
    finish: CardFinish
    listings: int
    quantity: int
    lowest_price_cents: int
