# ----------------------------------------------------------------
# RATINGS
# ----------------------------------------------------------------
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from models.database import RatingRole
from models.model import _ORMBase


class RatingCreate(BaseModel):
    # The ratee and their role are derived from the order: the buyer rates
    # the seller and vice versa.
    order_id: uuid.UUID
    rating: int = Field(ge=1, le=5)
    comment: str | None = Field(default=None, max_length=2000)


class RatingRead(_ORMBase, RatingCreate):
    id: uuid.UUID
    rater_id: uuid.UUID
    ratee_id: uuid.UUID
    role: RatingRole
    created_at: datetime