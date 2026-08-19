import uuid
from datetime import datetime

from pydantic import BaseModel

from models.model import _ORMBase


# ----------------------------------------------------------------
# ADDRESSES
# ----------------------------------------------------------------
class AddressCreate(BaseModel):
    label: str | None = None
    line1: str
    line2: str | None = None
    city: str
    state: str | None = None
    postal_code: str
    country: str
    is_default: bool = False


class AddressRead(_ORMBase, AddressCreate):
    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
