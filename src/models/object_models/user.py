"""
Pydantic schemas defining API request/response shapes.

Naming convention:
  *Read   - what the API returns
  *Create - what a client sends to create a record
  *Update - what a client sends to patch a record (all fields optional)

These intentionally do NOT mirror the ORM models 1:1 — fields like
seller_id or created_at come from the server/auth context, not the
client, so they're absent from Create schemas. Sensitive fields like
stripe_customer_id are absent from Read schemas entirely.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from models.database import AccountStatus, OrderStatus
from models.model import _ORMBase


# ----------------------------------------------------------------
# USERS
# ----------------------------------------------------------------
class UserRead(_ORMBase):
    id: uuid.UUID | None
    username: str
    display_name: str | None
    avatar_url: str | None
    bio: str | None
    account_status: AccountStatus
    seller_rating_avg: float | None
    seller_rating_count: int
    buyer_rating_avg: float | None
    buyer_rating_count: int
    total_sales: int
    created_at: datetime | None = None


class UserUpdate(BaseModel):
    display_name: str | None = None
    avatar_url: str | None = None
    bio: str | None = None




