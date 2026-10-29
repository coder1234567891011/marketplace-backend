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

from pydantic import BaseModel, Field

from models.database import AccountStatus
from models.model import _ORMBase


# ----------------------------------------------------------------
# USERS
# ----------------------------------------------------------------
class UserRead(_ORMBase):
    id: uuid.UUID
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


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9_]+$")
    display_name: str | None = Field(default=None, max_length=60)
    avatar_url: str | None = None
    bio: str | None = Field(default=None, max_length=1000)


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=60)
    avatar_url: str | None = None
    bio: str | None = Field(default=None, max_length=1000)
