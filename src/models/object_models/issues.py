# ----------------------------------------------------------------
# ORDER ISSUES
# ----------------------------------------------------------------
import enum
import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from models.database import IssueReason, IssueResolution, IssueStatus
from models.model import _ORMBase


class IssueCreate(BaseModel):
    reason: IssueReason
    # What went wrong; becomes the first message.
    message: str = Field(min_length=1, max_length=4000)


class IssueMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class IssueRefund(BaseModel):
    # Defaults to everything not yet refunded on the order.
    amount_cents: int | None = Field(default=None, ge=1)


class IssueMessageRead(_ORMBase):
    id: uuid.UUID
    author_id: uuid.UUID
    # Written by a moderator rather than the buyer or seller.
    from_moderator: bool = False
    body: str
    created_at: datetime


class IssueRead(_ORMBase):
    id: uuid.UUID
    order_id: uuid.UUID
    buyer_id: uuid.UUID
    seller_id: uuid.UUID
    reason: IssueReason
    status: IssueStatus
    resolution: IssueResolution | None
    refund_cents: int
    # When the buyer may escalate it (the seller can any time).
    escalate_after: datetime
    escalated_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class IssueDetail(IssueRead):
    messages: list[IssueMessageRead]


class IssueRole(str, enum.Enum):
    buyer = "buyer"
    seller = "seller"
