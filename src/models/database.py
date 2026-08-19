"""
SQLAlchemy 2.0 ORM models mirroring the Postgres/Supabase schema.

These represent database rows and relationships. They are NOT what
gets sent over the API — see schemas.py for the Pydantic models that
define request/response shapes.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ----------------------------------------------------------------
# Enums (mirror the CHECK constraints in the SQL schema)
# ----------------------------------------------------------------
class AccountStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    banned = "banned"


class CardCondition(str, enum.Enum):
    NM = "NM"
    LP = "LP"
    MP = "MP"
    HP = "HP"
    DMG = "DMG"


class RecognitionStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    manual = "manual"
    failed = "failed"


class ListingStatus(str, enum.Enum):
    active = "active"
    sold = "sold"
    removed = "removed"
    pending_sale = "pending_sale"


class OrderStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    shipped = "shipped"
    delivered = "delivered"
    disputed = "disputed"
    refunded = "refunded"
    cancelled = "cancelled"


class RatingRole(str, enum.Enum):
    buyer = "buyer"
    seller = "seller"


# ----------------------------------------------------------------
# USERS
# ----------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth.users.id", ondelete="CASCADE"), primary_key=True
    )
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    bio: Mapped[str | None] = mapped_column(Text)

    stripe_customer_id: Mapped[str | None] = mapped_column(String)
    stripe_connect_account_id: Mapped[str | None] = mapped_column(String)
    stripe_connect_onboarded: Mapped[bool] = mapped_column(default=False)

    account_status: Mapped[AccountStatus] = mapped_column(default=AccountStatus.active)
    email_verified: Mapped[bool] = mapped_column(default=False)

    seller_rating_avg: Mapped[float | None] = mapped_column(Numeric(3, 2))
    seller_rating_count: Mapped[int] = mapped_column(Integer, default=0)
    buyer_rating_avg: Mapped[float | None] = mapped_column(Numeric(3, 2))
    buyer_rating_count: Mapped[int] = mapped_column(Integer, default=0)
    total_sales: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime| None] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime| None] = mapped_column(server_default=text("now()"))
    last_active_at: Mapped[datetime | None]

    addresses: Mapped[list["Address"]] = relationship(back_populates="user")
    collections: Mapped[list["Collection"]] = relationship(back_populates="user")
    listings: Mapped[list["Listing"]] = relationship(back_populates="seller")


# ----------------------------------------------------------------
# ADDRESSES
# ----------------------------------------------------------------
class Address(Base):
    __tablename__ = "addresses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    label: Mapped[str | None] = mapped_column(String)
    line1: Mapped[str] = mapped_column(String, nullable=False)
    line2: Mapped[str | None] = mapped_column(String)
    city: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str | None] = mapped_column(String)
    postal_code: Mapped[str] = mapped_column(String, nullable=False)
    country: Mapped[str] = mapped_column(String, nullable=False)
    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    user: Mapped["User"] = relationship(back_populates="addresses")


# ----------------------------------------------------------------
# CARDS (local cache of Scryfall data)
# ----------------------------------------------------------------
class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (UniqueConstraint("set_code", "collector_number"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scryfall_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    set_code: Mapped[str] = mapped_column(String, nullable=False)
    collector_number: Mapped[str] = mapped_column(String, nullable=False)
    rarity: Mapped[str | None] = mapped_column(String)
    image_url: Mapped[str | None] = mapped_column(Text)
    is_foil_available: Mapped[bool] = mapped_column(default=True)
    last_synced_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


# ----------------------------------------------------------------
# COLLECTIONS
# ----------------------------------------------------------------
class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String, default="My Collection")
    is_public: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    user: Mapped["User"] = relationship(back_populates="collections")
    items: Mapped[list["CollectionItem"]] = relationship(back_populates="collection")


# ----------------------------------------------------------------
# COLLECTION ITEMS
# ----------------------------------------------------------------
class CollectionItem(Base):
    __tablename__ = "collection_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("collections.id", ondelete="CASCADE"))
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id"))

    condition: Mapped[CardCondition | None]
    is_foil: Mapped[bool] = mapped_column(default=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    scan_image_url: Mapped[str | None] = mapped_column(Text)
    recognition_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    recognition_status: Mapped[RecognitionStatus] = mapped_column(default=RecognitionStatus.pending)

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    collection: Mapped["Collection"] = relationship(back_populates="items")
    card: Mapped["Card"] = relationship()


# ----------------------------------------------------------------
# LISTINGS
# ----------------------------------------------------------------
class Listing(Base):
    __tablename__ = "listings"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    collection_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("collection_items.id"))
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id"))

    condition: Mapped[CardCondition]
    is_foil: Mapped[bool] = mapped_column(default=False)
    quantity_available: Mapped[int] = mapped_column(Integer, default=1)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String, default="usd")

    status: Mapped[ListingStatus] = mapped_column(default=ListingStatus.active)

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    seller: Mapped["User"] = relationship(back_populates="listings")
    card: Mapped["Card"] = relationship()


# ----------------------------------------------------------------
# ORDERS
# ----------------------------------------------------------------
class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))

    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String, unique=True)
    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    shipping_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[OrderStatus] = mapped_column(default=OrderStatus.pending)
    shipping_address_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("addresses.id"))
    tracking_number: Mapped[str | None] = mapped_column(String)

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    items: Mapped[list["OrderItem"]] = relationship(back_populates="order")


# ----------------------------------------------------------------
# ORDER ITEMS
# ----------------------------------------------------------------
class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped["Order"] = relationship(back_populates="items")


# ----------------------------------------------------------------
# RATINGS
# ----------------------------------------------------------------
class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("order_id", "rater_id", "ratee_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    rater_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    ratee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    role: Mapped[RatingRole]
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))