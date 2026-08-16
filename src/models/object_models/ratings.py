# ----------------------------------------------------------------
# RATINGS
# ----------------------------------------------------------------
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from models.database import RatingRole
from models.model import _ORMBase


class RatingCreate(BaseModel):
    order_id: uuid.UUID
    ratee_id: uuid.UUID
    role: RatingRole
    rating: int = Field(ge=1, le=5)
    comment: str | None = None


class RatingRead(_ORMBase, RatingCreate):
    id: uuid.UUID
    rater_id: uuid.UUID
    created_at: datetime