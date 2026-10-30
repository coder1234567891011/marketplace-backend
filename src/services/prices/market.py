"""
Marketplace prices, worked out from sales on this platform.

A card's market price is per finish and language, in NM condition: sales in
other conditions are scaled up to NM (settings.condition_price_factors)
before taking the median, so an LP sale still counts. Run nightly by
scripts/market_prices.py.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import Date, Double, Integer, Numeric, Text, cast, column, delete, func, literal, select, tuple_, values
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import (
    CardCondition,
    Listing,
    MarketPrice,
    Order,
    OrderItem,
    OrderStatus,
    PriceSnapshot,
    PriceSource,
    User,
)
from services.listings.listings import for_sale_conditions
from utils.settings import settings

# Orders whose sale counts: paid and not refunded, cancelled or disputed.
SOLD_STATUSES = (OrderStatus.paid, OrderStatus.shipped, OrderStatus.delivered)


def condition_factor(condition: CardCondition) -> Decimal:
    return settings.condition_price_factors[condition.value]


def price_for_condition(nm_price_cents: int, condition: CardCondition) -> int:
    """An NM price scaled to another condition, in whole cents."""
    scaled = Decimal(nm_price_cents) * condition_factor(condition)
    return int(scaled.quantize(Decimal(1), rounding=ROUND_HALF_UP))


@dataclass
class Recomputed:
    from_sales: int
    from_listings: int
    snapshots: int


async def recompute_market_prices(session: AsyncSession, now: datetime) -> Recomputed:
    """Rebuild market_prices, and record today's prices that changed in price_snapshots."""
    keys = (OrderItem.card_id, OrderItem.finish, OrderItem.language)
    factors = values(column("condition", Text), column("factor", Numeric), name="factors").data(
        [(c, f) for c, f in settings.condition_price_factors.items()])

    # Each sale in the window as an NM price, numbered newest first per card, finish and language.
    ranked = (
        select(*keys, (OrderItem.price_cents / factors.c.factor).label("nm_price"),
               func.row_number().over(partition_by=keys, order_by=(Order.paid_at.desc(), OrderItem.id)).label("rn"))
        .join(Order, Order.id == OrderItem.order_id)
        .join(factors, factors.c.condition == OrderItem.condition)
        .where(Order.status.in_(SOLD_STATUSES),
               Order.paid_at >= now - timedelta(days=settings.market_price_days))
    ).subquery()
    median = func.percentile_cont(0.5).within_group(cast(ranked.c.nm_price, Double))
    from_sales = (
        select(ranked.c.card_id, ranked.c.finish, ranked.c.language,
               cast(func.round(median), Integer).label("price_cents"),
               literal(PriceSource.sales.value).label("source"), func.count().label("sales_count"))
        .where(ranked.c.rn <= settings.market_price_sales)
        .group_by(ranked.c.card_id, ranked.c.finish, ranked.c.language)
        .having(func.count() >= settings.market_price_min_sales)
    ).cte("from_sales")

    listing_keys = (Listing.card_id, Listing.finish, Listing.language)
    from_listings = (
        select(*listing_keys, func.min(Listing.price_cents), literal(PriceSource.listings.value), literal(0))
        .join(User, User.id == Listing.seller_id)
        .where(*for_sale_conditions(), Listing.condition == CardCondition.NM,
               tuple_(*listing_keys).not_in(select(from_sales.c.card_id, from_sales.c.finish, from_sales.c.language)))
        .group_by(*listing_keys)
    )

    await session.execute(delete(MarketPrice))
    columns = ["card_id", "finish", "language", "price_cents", "source", "sales_count"]
    await session.execute(insert(MarketPrice).from_select(columns, select(from_sales).union_all(from_listings)))
    counts = dict((await session.execute(
        select(MarketPrice.source, func.count()).group_by(MarketPrice.source))).tuples().all())

    # Only prices that differ from the latest snapshot: history stays small.
    latest = (
        select(PriceSnapshot.price_cents)
        .where(PriceSnapshot.card_id == MarketPrice.card_id, PriceSnapshot.finish == MarketPrice.finish,
               PriceSnapshot.language == MarketPrice.language)
        .order_by(PriceSnapshot.day.desc()).limit(1)
    ).scalar_subquery()
    changed = (select(MarketPrice.card_id, MarketPrice.finish, MarketPrice.language, literal(now.date(), Date),
                      MarketPrice.price_cents, MarketPrice.source)
               .where(latest.is_distinct_from(MarketPrice.price_cents)))
    stmt = insert(PriceSnapshot).from_select(
        ["card_id", "finish", "language", "day", "price_cents", "source"], changed)
    result = await session.execute(stmt.on_conflict_do_update(
        index_elements=["card_id", "finish", "language", "day"],
        set_={"price_cents": stmt.excluded.price_cents, "source": stmt.excluded.source}))
    return Recomputed(from_sales=counts.get(PriceSource.sales, 0), from_listings=counts.get(PriceSource.listings, 0),
                      snapshots=result.rowcount)  # type: ignore[attr-defined]
