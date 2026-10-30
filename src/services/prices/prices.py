"""Reading marketplace prices: a card's market prices, its recent sales, its
price history and a suggested price for a new listing."""
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import CardCondition, CardFinish, MarketPrice, Order, OrderItem, PriceSnapshot
from models.object_models.prices import SaleRead, SuggestedPrice
from services.cards.cards import get_card
from services.prices.market import SOLD_STATUSES, price_for_condition

# Scryfall's price key for each finish (TCGplayer, US dollars).
REFERENCE_PRICE_KEYS = {CardFinish.nonfoil: "usd", CardFinish.foil: "usd_foil", CardFinish.etched: "usd_etched"}


async def market_prices(session: AsyncSession, card_id: uuid.UUID) -> Sequence[MarketPrice]:
    stmt = (select(MarketPrice).where(MarketPrice.card_id == card_id)
            .order_by(MarketPrice.finish, MarketPrice.language))
    return (await session.scalars(stmt)).all()


async def recent_sales(
    session: AsyncSession, card_id: uuid.UUID, limit: int, finish: CardFinish | None = None,
    language: str | None = None, condition: CardCondition | None = None,
) -> list[SaleRead]:
    """The card's latest sales, newest first."""
    await get_card(session, card_id)
    stmt = (
        select(OrderItem.condition, OrderItem.finish, OrderItem.language, OrderItem.quantity, OrderItem.price_cents,
               Order.paid_at)
        .join(Order, Order.id == OrderItem.order_id)
        .where(OrderItem.card_id == card_id, Order.status.in_(SOLD_STATUSES))
        .order_by(Order.paid_at.desc(), OrderItem.id).limit(limit)
    )
    if finish:
        stmt = stmt.where(OrderItem.finish == finish)
    if language:
        stmt = stmt.where(OrderItem.language == language)
    if condition:
        stmt = stmt.where(OrderItem.condition == condition)
    return [SaleRead(condition=r.condition, finish=r.finish, language=r.language, quantity=r.quantity,
                     price_cents=r.price_cents, sold_on=r.paid_at.date())
            for r in await session.execute(stmt)]


async def price_history(
    session: AsyncSession, card_id: uuid.UUID, finish: CardFinish, language: str, days: int, today: date,
) -> Sequence[PriceSnapshot]:
    """Price changes over the last days, oldest first, starting with the
    price in force when the period began (which may be dated earlier)."""
    await get_card(session, card_id)
    start = today - timedelta(days=days)
    key = (PriceSnapshot.card_id == card_id, PriceSnapshot.finish == finish, PriceSnapshot.language == language)
    opening = await session.scalar(select(PriceSnapshot).where(*key, PriceSnapshot.day <= start)
                                   .order_by(PriceSnapshot.day.desc()).limit(1))
    changes = (await session.scalars(select(PriceSnapshot).where(*key, PriceSnapshot.day > start)
                                     .order_by(PriceSnapshot.day))).all()
    return [opening, *changes] if opening else changes


async def suggested_price(
    session: AsyncSession, card_id: uuid.UUID, condition: CardCondition, finish: CardFinish, language: str,
) -> SuggestedPrice:
    """The market price scaled to the condition; without one, Scryfall's
    reference price (for the printing's own language only)."""
    card = await get_card(session, card_id)
    market = await session.get(MarketPrice, (card_id, finish, language))
    if market is not None:
        return SuggestedPrice(price_cents=price_for_condition(market.price_cents, condition),
                              source=market.source.value, sales_count=market.sales_count)
    reference = card.prices.prices.get(REFERENCE_PRICE_KEYS[finish]) if card.prices else None
    if reference and language == card.lang:
        nm_cents = int(Decimal(reference) * 100)
        return SuggestedPrice(price_cents=price_for_condition(nm_cents, condition), source="reference")
    return SuggestedPrice(price_cents=None, source=None)
