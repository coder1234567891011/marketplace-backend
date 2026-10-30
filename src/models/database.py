"""
SQLAlchemy 2.0 ORM models mirroring the Postgres/Supabase schema.

These represent database rows and relationships. They are NOT what
gets sent over the API — see models/object_models/ for the Pydantic
models that define request/response shapes.

Relationships use lazy="raise": under asyncio an implicit lazy load fails
anyway, so services must eager-load (selectinload/joinedload) what they
return.
"""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    Table,
    Text,
    TypeDecorator,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    # Postgres's own default constraint names, which the production schema
    # (created by hand in Supabase before Alembic) already uses.
    metadata = MetaData(naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "%(table_name)s_%(column_0_N_name)s_key",
        "ck": "%(table_name)s_%(constraint_name)s_check",
        "fk": "%(table_name)s_%(column_0_name)s_fkey",
        "pk": "%(table_name)s_pkey",
    })
    type_annotation_map = {
        str: Text(),
        datetime: DateTime(timezone=True),
    }
    # Fetch server-generated values (created_at, updated_at) via RETURNING on
    # INSERT and UPDATE, so they never need an implicit (async-unsafe) reload.
    __mapper_args__ = {"eager_defaults": True}


# Supabase's auth.users table, declared so users.id can reference it.
# Migrations never create or alter it in production (see migrations/env.py).
auth_users = Table(
    "users",
    Base.metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("email_confirmed_at", DateTime(timezone=True)),
    schema="auth",
)


class TextEnum(TypeDecorator[enum.Enum]):
    """A Python enum stored in a plain TEXT column (allowed values are
    enforced by a CHECK constraint, see enum_check)."""
    impl = Text
    cache_ok = True

    def __init__(self, enum_cls: type[enum.Enum]):
        super().__init__()
        self.enum_cls = enum_cls

    def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
        return None if value is None else self.enum_cls(value).value

    def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
        return None if value is None else self.enum_cls(value)


def enum_check(column: str, enum_cls: type[enum.Enum]) -> CheckConstraint:
    allowed = ", ".join(f"'{m.value}'" for m in enum_cls)
    return CheckConstraint(f"{column} IN ({allowed})", name=column)


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


class CardFinish(str, enum.Enum):
    nonfoil = "nonfoil"
    foil = "foil"
    etched = "etched"


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


class CheckoutStatus(str, enum.Enum):
    open = "open"          # waiting for payment; the copies are held
    paid = "paid"
    expired = "expired"    # not paid in time, or cancelled; the copies were released


class RatingRole(str, enum.Enum):
    buyer = "buyer"
    seller = "seller"


class PriceSource(str, enum.Enum):
    sales = "sales"          # median of recent sales on the marketplace
    listings = "listings"    # not enough sales: the cheapest NM listing for sale


# ----------------------------------------------------------------
# USERS
# ----------------------------------------------------------------
class User(Base):
    __tablename__ = "users"
    __table_args__ = (enum_check("account_status", AccountStatus),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth.users.id", ondelete="CASCADE"), primary_key=True
    )
    username: Mapped[str] = mapped_column(unique=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column()
    avatar_url: Mapped[str | None] = mapped_column()
    bio: Mapped[str | None] = mapped_column()

    stripe_customer_id: Mapped[str | None] = mapped_column()
    stripe_connect_account_id: Mapped[str | None] = mapped_column()
    stripe_connect_onboarded: Mapped[bool] = mapped_column(default=False, server_default=text("false"))

    account_status: Mapped[AccountStatus] = mapped_column(
        TextEnum(AccountStatus), default=AccountStatus.active, server_default=text("'active'")
    )
    email_verified: Mapped[bool] = mapped_column(default=False, server_default=text("false"))

    seller_rating_avg: Mapped[float | None] = mapped_column(Numeric(3, 2))
    seller_rating_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    buyer_rating_avg: Mapped[float | None] = mapped_column(Numeric(3, 2))
    buyer_rating_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    total_sales: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"), onupdate=func.now())
    last_active_at: Mapped[datetime | None]

    addresses: Mapped[list[Address]] = relationship(back_populates="user", lazy="raise")
    collections: Mapped[list[Collection]] = relationship(back_populates="user", lazy="raise")
    listings: Mapped[list[Listing]] = relationship(back_populates="seller", lazy="raise")


# ----------------------------------------------------------------
# ADDRESSES
# ----------------------------------------------------------------
class Address(Base):
    __tablename__ = "addresses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    label: Mapped[str | None] = mapped_column()
    recipient_name: Mapped[str | None] = mapped_column()
    line1: Mapped[str] = mapped_column(nullable=False)
    line2: Mapped[str | None] = mapped_column()
    city: Mapped[str] = mapped_column(nullable=False)
    state: Mapped[str | None] = mapped_column()
    postal_code: Mapped[str] = mapped_column(nullable=False)
    country: Mapped[str] = mapped_column(nullable=False)
    is_default: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    user: Mapped[User] = relationship(back_populates="addresses", lazy="raise")


# ----------------------------------------------------------------
# CARDS (local cache of Scryfall data)
# ----------------------------------------------------------------
class Card(Base):
    """One printing of a card, synced nightly from Scryfall (scripts/scryfall_sync.py)."""
    __tablename__ = "cards"
    __table_args__ = (
        UniqueConstraint("set_code", "collector_number", "lang"),
        # Full-text search on card names.
        Index("idx_cards_name", text("to_tsvector('english'::regconfig, name)"), postgresql_using="gin"),
        # Substring and typo-tolerant name matching (pg_trgm), for autocomplete.
        Index("idx_cards_name_trgm", "name", postgresql_using="gin", postgresql_ops={"name": "gin_trgm_ops"}),
        Index("idx_cards_oracle_id", "oracle_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scryfall_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True, nullable=False)
    # Shared by every printing of the same card.
    oracle_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(nullable=False)
    set_code: Mapped[str] = mapped_column(nullable=False)
    set_name: Mapped[str] = mapped_column(nullable=False)
    # Scryfall's set type: core, expansion, masters, commander, promo, funny, …
    set_type: Mapped[str | None] = mapped_column()
    collector_number: Mapped[str] = mapped_column(nullable=False)
    lang: Mapped[str] = mapped_column(nullable=False, server_default=text("'en'::text"))
    released_at: Mapped[date] = mapped_column(nullable=False)
    rarity: Mapped[str | None] = mapped_column()
    promo: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    layout: Mapped[str] = mapped_column(nullable=False)
    mana_cost: Mapped[str | None] = mapped_column()
    cmc: Mapped[float | None] = mapped_column(Double)
    type_line: Mapped[str | None] = mapped_column()
    oracle_text: Mapped[str | None] = mapped_column()
    colors: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    color_identity: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    # Which of nonfoil / foil / etched this printing exists in.
    finishes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default=text("'{}'::text[]"))
    # Scryfall CDN links (small, normal); images are hotlinked, not stored.
    image_uris: Mapped[dict[str, str] | None] = mapped_column(JSONB(none_as_null=True))
    # Per-face name, cost, type, text and images for double-faced cards.
    card_faces: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB(none_as_null=True))
    # When the sync last changed this row.
    last_synced_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    prices: Mapped[CardPrice | None] = relationship(lazy="raise")


class CardPrice(Base):
    """Scryfall's TCGplayer/Cardmarket prices, shown only as an outside reference.

    Kept out of cards because they change daily: the nightly sync rewrites
    these small rows instead of the whole catalog, which keeps table bloat
    down on Supabase's 500 MB free database.
    """
    __tablename__ = "card_prices"

    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True)
    # e.g. {"usd": "0.25", "usd_foil": "1.10", "eur": "0.20"}; missing prices are left out.
    prices: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class MarketPrice(Base):
    """A card's price on the marketplace, in NM condition, per finish and
    language. Rebuilt nightly by scripts/market_prices.py; other conditions
    are priced with settings.condition_price_factors."""
    __tablename__ = "market_prices"
    __table_args__ = (
        enum_check("finish", CardFinish),
        enum_check("source", PriceSource),
    )

    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True)
    finish: Mapped[CardFinish] = mapped_column(TextEnum(CardFinish), primary_key=True)
    language: Mapped[str] = mapped_column(primary_key=True)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[PriceSource] = mapped_column(TextEnum(PriceSource))
    # Sales the price was taken from (0 when it comes from listings).
    sales_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    computed_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


class PriceSnapshot(Base):
    """Market price history, for charts. A row is only written on days the
    price changed, so the price on any day is the latest row on or before it."""
    __tablename__ = "price_snapshots"
    __table_args__ = (
        enum_check("finish", CardFinish),
        enum_check("source", PriceSource),
    )

    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True)
    finish: Mapped[CardFinish] = mapped_column(TextEnum(CardFinish), primary_key=True)
    language: Mapped[str] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[PriceSource] = mapped_column(TextEnum(PriceSource))


# ----------------------------------------------------------------
# COLLECTIONS
# ----------------------------------------------------------------
class Collection(Base):
    __tablename__ = "collections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(default="My Collection", server_default=text("'My Collection'"))
    is_public: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"), onupdate=func.now())

    user: Mapped[User] = relationship(back_populates="collections", lazy="raise")
    items: Mapped[list[CollectionItem]] = relationship(back_populates="collection", lazy="raise")


# ----------------------------------------------------------------
# COLLECTION ITEMS
# ----------------------------------------------------------------
class CollectionItem(Base):
    __tablename__ = "collection_items"
    __table_args__ = (
        enum_check("condition", CardCondition),
        enum_check("finish", CardFinish),
        enum_check("recognition_status", RecognitionStatus),
        Index("idx_collection_items_collection", "collection_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    collection_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("collections.id", ondelete="CASCADE"))
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id"))

    condition: Mapped[CardCondition | None] = mapped_column(TextEnum(CardCondition))
    finish: Mapped[CardFinish] = mapped_column(
        TextEnum(CardFinish), default=CardFinish.nonfoil, server_default=text("'nonfoil'")
    )
    # Language of the physical card (Scryfall code: en, ja, de, …). Usually
    # the same as card.lang, but Scryfall lists most cards in English only.
    language: Mapped[str] = mapped_column(default="en", server_default=text("'en'"))
    quantity: Mapped[int] = mapped_column(Integer, default=1)

    scan_image_url: Mapped[str | None] = mapped_column()
    recognition_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    recognition_status: Mapped[RecognitionStatus] = mapped_column(
        TextEnum(RecognitionStatus), default=RecognitionStatus.pending, server_default=text("'pending'")
    )

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    collection: Mapped[Collection] = relationship(back_populates="items", lazy="raise")
    card: Mapped[Card] = relationship(lazy="raise")


# ----------------------------------------------------------------
# LISTINGS
# ----------------------------------------------------------------
class Listing(Base):
    __tablename__ = "listings"
    __table_args__ = (
        enum_check("condition", CardCondition),
        enum_check("finish", CardFinish),
        enum_check("status", ListingStatus),
        Index("idx_listings_card", "card_id"),
        # Browsing a card's listings, cheapest first.
        Index("idx_listings_card_price", "card_id", "price_cents", postgresql_where=text("status = 'active'")),
        Index("idx_listings_seller", "seller_id", "created_at"),
        Index("idx_listings_collection_item", "collection_item_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    collection_item_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("collection_items.id"))
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id"))

    condition: Mapped[CardCondition] = mapped_column(TextEnum(CardCondition))
    finish: Mapped[CardFinish] = mapped_column(
        TextEnum(CardFinish), default=CardFinish.nonfoil, server_default=text("'nonfoil'")
    )
    language: Mapped[str] = mapped_column(default="en", server_default=text("'en'"))
    # Copies buyers can still add to a checkout.
    quantity_available: Mapped[int] = mapped_column(Integer, default=1)
    # Copies held by open checkouts. Payment sells them; expiry returns them
    # to quantity_available.
    quantity_reserved: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(default="usd", server_default=text("'usd'"))
    notes: Mapped[str | None] = mapped_column()

    status: Mapped[ListingStatus] = mapped_column(
        TextEnum(ListingStatus), default=ListingStatus.active, server_default=text("'active'")
    )

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"), onupdate=func.now())

    seller: Mapped[User] = relationship(back_populates="listings", lazy="raise")
    card: Mapped[Card] = relationship(lazy="raise")


# ----------------------------------------------------------------
# SHIPPING RATES
# ----------------------------------------------------------------
class ShippingRate(Base):
    """A seller's flat shipping option, e.g. "Untracked envelope, $1"."""
    __tablename__ = "shipping_rates"
    __table_args__ = (Index("idx_shipping_rates_seller", "seller_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(default="usd", server_default=text("'usd'"))
    tracked: Mapped[bool] = mapped_column(default=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"), onupdate=func.now())


# ----------------------------------------------------------------
# CART
# ----------------------------------------------------------------
class CartItem(Base):
    """A listing in a buyer's cart. Nothing is held until checkout."""
    __tablename__ = "cart_items"
    __table_args__ = (UniqueConstraint("user_id", "listing_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id", ondelete="CASCADE"))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))

    listing: Mapped[Listing] = relationship(lazy="raise")


# ----------------------------------------------------------------
# CHECKOUTS
# ----------------------------------------------------------------
class Checkout(Base):
    """One payment by a buyer, covering one order per seller in their cart."""
    __tablename__ = "checkouts"
    __table_args__ = (
        enum_check("status", CheckoutStatus),
        Index("idx_checkouts_buyer", "buyer_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    status: Mapped[CheckoutStatus] = mapped_column(
        TextEnum(CheckoutStatus), default=CheckoutStatus.open, server_default=text("'open'")
    )
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(default="usd", server_default=text("'usd'"))
    stripe_checkout_session_id: Mapped[str | None] = mapped_column(unique=True)
    # Stripe's hosted payment page; only valid while open.
    stripe_checkout_url: Mapped[str | None] = mapped_column()
    stripe_payment_intent_id: Mapped[str | None] = mapped_column()
    # Seller transfers are funded from this charge.
    stripe_charge_id: Mapped[str | None] = mapped_column()
    expires_at: Mapped[datetime] = mapped_column(nullable=False)
    paid_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"), onupdate=func.now())

    orders: Mapped[list[Order]] = relationship(back_populates="checkout", lazy="raise")


# ----------------------------------------------------------------
# ORDERS
# ----------------------------------------------------------------
class Order(Base):
    """What a buyer bought from one seller in one checkout."""
    __tablename__ = "orders"
    __table_args__ = (
        enum_check("status", OrderStatus),
        Index("idx_orders_buyer", "buyer_id", "created_at"),
        Index("idx_orders_seller", "seller_id", "created_at"),
        Index("idx_orders_checkout", "checkout_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Null only for orders made before checkout existed.
    checkout_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("checkouts.id"))
    buyer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    seller_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))

    subtotal_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    shipping_cents: Mapped[int] = mapped_column(Integer, default=0)
    total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(default="usd", server_default=text("'usd'"))
    # The fee terms in force when the order was placed, and what they came to.
    fee_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal(0), server_default=text("0"))
    fee_fixed_cents: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    platform_fee_cents: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    seller_payout_cents: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))

    status: Mapped[OrderStatus] = mapped_column(
        TextEnum(OrderStatus), default=OrderStatus.pending, server_default=text("'pending'")
    )
    shipping_address_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("addresses.id"))
    # Copied at checkout, so editing or deleting the address can't change where an order goes.
    shipping_address: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    shipping_rate_name: Mapped[str | None] = mapped_column()
    tracking_number: Mapped[str | None] = mapped_column()

    stripe_transfer_id: Mapped[str | None] = mapped_column()
    stripe_refund_id: Mapped[str | None] = mapped_column()
    paid_at: Mapped[datetime | None]
    shipped_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    paid_out_at: Mapped[datetime | None]

    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(server_default=text("now()"), onupdate=func.now())

    checkout: Mapped[Checkout | None] = relationship(back_populates="orders", lazy="raise")
    items: Mapped[list[OrderItem]] = relationship(back_populates="order", lazy="raise")


# ----------------------------------------------------------------
# ORDER ITEMS
# ----------------------------------------------------------------
class OrderItem(Base):
    """Copies bought from one listing, as they were described at checkout."""
    __tablename__ = "order_items"
    __table_args__ = (
        enum_check("condition", CardCondition),
        enum_check("finish", CardFinish),
        Index("idx_order_items_order", "order_id"),
        Index("idx_order_items_card", "card_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"))
    listing_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("listings.id"))
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cards.id"))
    condition: Mapped[CardCondition] = mapped_column(TextEnum(CardCondition))
    finish: Mapped[CardFinish] = mapped_column(TextEnum(CardFinish))
    language: Mapped[str] = mapped_column(nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    # Price of one copy.
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped[Order] = relationship(back_populates="items", lazy="raise")
    card: Mapped[Card] = relationship(lazy="raise")


# ----------------------------------------------------------------
# STRIPE WEBHOOK EVENTS
# ----------------------------------------------------------------
class StripeEvent(Base):
    """Webhook events already handled. Stripe can deliver an event more than
    once; recording it in the same transaction makes handling idempotent."""
    __tablename__ = "stripe_events"

    id: Mapped[str] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))


# ----------------------------------------------------------------
# RATINGS
# ----------------------------------------------------------------
class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (
        UniqueConstraint("order_id", "rater_id", "ratee_id"),
        enum_check("role", RatingRole),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id"))
    rater_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    ratee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    role: Mapped[RatingRole] = mapped_column(TextEnum(RatingRole))
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(server_default=text("now()"))