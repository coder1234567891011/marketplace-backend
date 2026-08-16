# ----------------------------------------------------------------
# LISTINGS
# ----------------------------------------------------------------
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from models.database import CardCondition, ListingStatus
from models.object_models.cards import CardRead
from models.object_models.user import UserRead
from models.model import _ORMBase


class ListingCreate(BaseModel):
    card_id: uuid.UUID
    collection_item_id: uuid.UUID | None = None
    condition: CardCondition
    is_foil: bool = False
    quantity_available: int = Field(default=1, ge=1)
    price_cents: int = Field(gt=0)


class ListingRead(_ORMBase):
    id: uuid.UUID
    seller: UserRead
    card: CardRead
    condition: CardCondition
    is_foil: bool
    quantity_available: int
    price_cents: int
    currency: str
    status: ListingStatus
    created_at: datetime
