# ----------------------------------------------------------------
# COLLECTIONS
# ----------------------------------------------------------------
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from models.database import CardCondition, RecognitionStatus
from models.object_models.cards import CardRead
from models.model import _ORMBase


class CollectionCreate(BaseModel):
    name: str = "My Collection"
    is_public: bool = False


class CollectionRead(_ORMBase, CollectionCreate):
    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime



# ----------------------------------------------------------------
# COLLECTION ITEMS
# ----------------------------------------------------------------
class CollectionItemCreate(BaseModel):
    card_id: uuid.UUID
    condition: CardCondition | None = None
    is_foil: bool = False
    quantity: int = Field(default=1, ge=1)
    scan_image_url: str | None = None


class CollectionItemRead(_ORMBase):
    id: uuid.UUID
    collection_id: uuid.UUID
    card: CardRead
    condition: CardCondition | None
    is_foil: bool
    quantity: int
    recognition_confidence: float | None
    recognition_status: RecognitionStatus
    created_at: datetime

