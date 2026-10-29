# ----------------------------------------------------------------
# LISTINGS
# ----------------------------------------------------------------
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from models.database import CardCondition, ListingStatus
from models.model import _ORMBase
from models.object_models.cards import CardRead
from models.object_models.user import UserRead


class ListingCreate(BaseModel):
    card_id: uuid.UUID
    collection_item_id: uuid.UUID | None = None
    condition: CardCondition
    is_foil: bool = False
    quantity_available: int = Field(default=1, ge=1)
    price_cents: int = Field(gt=0)


class ListingUpdate(BaseModel):
    condition: CardCondition | None = None
    is_foil: bool | None = None
    quantity_available: int | None = Field(default=None, ge=1)
    price_cents: int | None = Field(default=None, gt=0)


class ListingRead(_ORMBase):
    id: uuid.UUID
    collection_item_id: uuid.UUID | None
    seller: UserRead
    card: CardRead
    condition: CardCondition
    is_foil: bool
    quantity_available: int
    price_cents: int
    currency: str
    status: ListingStatus
    created_at: datetime
    updated_at: datetime
