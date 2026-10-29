# ----------------------------------------------------------------
# SHIPPING RATES
# ----------------------------------------------------------------
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from models.model import _ORMBase


class ShippingRateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=60, examples=["Untracked envelope"])
    price_cents: int = Field(ge=0, le=100_000)
    tracked: bool = False


class ShippingRateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    price_cents: int | None = Field(default=None, ge=0, le=100_000)
    tracked: bool | None = None


class ShippingRateRead(_ORMBase):
    id: uuid.UUID
    seller_id: uuid.UUID
    name: str
    price_cents: int
    currency: str
    tracked: bool
    created_at: datetime
    updated_at: datetime
