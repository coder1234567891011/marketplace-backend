import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import (
    CardCondition,
    CardFinish,
    CardPrice,
    Listing,
    ListingStatus,
    MarketPrice,
    Order,
    OrderItem,
    OrderStatus,
    PriceSnapshot,
)
from services.prices.market import price_for_condition, recompute_market_prices
from utils.settings import Settings, settings

NOW = datetime.now(timezone.utc)


async def _listing(db: AsyncSession, seller: uuid.UUID, card: uuid.UUID, price_cents: int,
                   condition: str = "NM", finish: str = "nonfoil", language: str = "en",
                   status: ListingStatus = ListingStatus.active) -> Listing:
    listing = Listing(seller_id=seller, card_id=card, condition=CardCondition(condition), finish=CardFinish(finish),
                      language=language, price_cents=price_cents, status=status,
                      quantity_available=0 if status == ListingStatus.sold else 1)
    db.add(listing)
    await db.flush()
    return listing


async def _sale(db: AsyncSession, buyer: uuid.UUID, seller: uuid.UUID, card: uuid.UUID, price_cents: int,
                condition: str = "NM", finish: str = "nonfoil", language: str = "en", days_ago: float = 1,
                status: OrderStatus = OrderStatus.paid, quantity: int = 1) -> None:
    listing = await _listing(db, seller, card, price_cents, condition, finish, language, ListingStatus.sold)
    order = Order(buyer_id=buyer, seller_id=seller, subtotal_cents=price_cents * quantity,
                  total_cents=price_cents * quantity, status=status, paid_at=NOW - timedelta(days=days_ago))
    db.add(order)
    await db.flush()
    db.add(OrderItem(order_id=order.id, listing_id=listing.id, card_id=card, condition=listing.condition,
                     finish=listing.finish, language=language, quantity=quantity, price_cents=price_cents))
    await db.flush()


async def _market(db: AsyncSession, card: uuid.UUID) -> dict[tuple[str, str], tuple[int, str, int]]:
    rows = await db.scalars(select(MarketPrice).where(MarketPrice.card_id == card))
    return {(m.finish.value, m.language): (m.price_cents, m.source.value, m.sales_count) for m in rows}


def test_condition_factors() -> None:
    assert price_for_condition(1000, CardCondition.NM) == 1000
    assert price_for_condition(1005, CardCondition.LP) == 905  # 904.5 rounds up
    with pytest.raises(ValidationError):
        Settings(condition_price_factors={"NM": Decimal(1)})
    with pytest.raises(ValidationError):
        Settings(condition_price_factors={"NM": 1, "LP": 0, "MP": 1, "HP": 1, "DMG": 1})


async def test_market_price_from_sales(db: AsyncSession, make_user, make_card) -> None:
    buyer, seller = await make_user(), await make_user()
    card = await make_card()
    await _sale(db, buyer, seller, card, 1000, days_ago=3)
    await _sale(db, buyer, seller, card, 900, condition="LP", days_ago=2)   # 1000 as NM
    await _sale(db, buyer, seller, card, 1200, days_ago=1)
    # Not counted: too old, refunded, disputed.
    await _sale(db, buyer, seller, card, 100, days_ago=settings.market_price_days + 1)
    await _sale(db, buyer, seller, card, 5000, status=OrderStatus.refunded)
    await _sale(db, buyer, seller, card, 5000, status=OrderStatus.disputed)
    # Other finishes and languages are priced on their own; two sales aren't enough.
    await _sale(db, buyer, seller, card, 3000, finish="foil")
    await _sale(db, buyer, seller, card, 3000, finish="foil")

    result = await recompute_market_prices(db, NOW)
    assert (result.from_sales, result.from_listings, result.snapshots) == (1, 0, 1)
    assert await _market(db, card) == {("nonfoil", "en"): (1000, "sales", 3)}


async def test_market_price_uses_latest_sales(db: AsyncSession, make_user, make_card,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "market_price_sales", 2)
    monkeypatch.setattr(settings, "market_price_min_sales", 2)
    buyer, seller = await make_user(), await make_user()
    card = await make_card()
    for days_ago, price in ((3, 100), (2, 1000), (1, 2000)):
        await _sale(db, buyer, seller, card, price, days_ago=days_ago)
    await recompute_market_prices(db, NOW)
    assert await _market(db, card) == {("nonfoil", "en"): (1500, "sales", 2)}


async def test_market_price_from_listings(db: AsyncSession, make_user, make_card) -> None:
    buyer, seller, other = await make_user(), await make_user(), await make_user()
    unpayable = await make_user(can_sell=False)
    card = await make_card()
    await _sale(db, buyer, seller, card, 2000)  # one sale isn't enough
    await _listing(db, seller, card, 700)
    await _listing(db, other, card, 500)
    await _listing(db, seller, card, 100, condition="LP")
    await _listing(db, unpayable, card, 50)
    await _listing(db, seller, card, 50, status=ListingStatus.removed)
    await _listing(db, seller, card, 900, language="ja")

    result = await recompute_market_prices(db, NOW)
    assert (result.from_sales, result.from_listings) == (0, 2)
    assert await _market(db, card) == {("nonfoil", "en"): (500, "listings", 0), ("nonfoil", "ja"): (900, "listings", 0)}

    # Prices that no longer have sales or listings disappear.
    await db.execute(update(Listing).where(Listing.card_id == card).values(status=ListingStatus.removed))
    await recompute_market_prices(db, NOW)
    assert await _market(db, card) == {}


async def test_price_history(client: AsyncClient, db: AsyncSession, make_user, make_card) -> None:
    seller = await make_user()
    card = await make_card()
    listing = await _listing(db, seller, card, 500)

    async def recompute(days_ago: int, price: int) -> int:
        listing.price_cents = price
        await db.flush()
        return (await recompute_market_prices(db, NOW - timedelta(days=days_ago))).snapshots

    assert await recompute(200, 400) == 1
    assert await recompute(100, 400) == 0  # unchanged: nothing written
    assert await recompute(30, 600) == 1
    assert await recompute(30, 650) == 1   # same day: replaces it
    assert await recompute(0, 700) == 1
    count = len((await db.scalars(select(PriceSnapshot).where(PriceSnapshot.card_id == card))).all())
    assert count == 3

    resp = await client.get(f"/v1/cards/{card}/price-history", params={"days": 90})
    assert resp.status_code == 200, resp.text
    points = [(p["price_cents"], p["source"]) for p in resp.json()]
    # Starts with the price in force 90 days ago.
    assert points == [(400, "listings"), (650, "listings"), (700, "listings")]
    assert (await client.get(f"/v1/cards/{card}/price-history", params={"finish": "foil"})).json() == []
    assert (await client.get(f"/v1/cards/{uuid.uuid4()}/price-history")).status_code == 404


async def test_card_prices_and_sales(client: AsyncClient, db: AsyncSession, make_user, make_card) -> None:
    buyer, seller = await make_user(), await make_user()
    card = await make_card()
    for days_ago, price, condition in ((3, 1000, "NM"), (2, 900, "LP"), (1, 1100, "NM")):
        await _sale(db, buyer, seller, card, price, condition=condition, days_ago=days_ago)
    await _sale(db, buyer, seller, card, 5000, status=OrderStatus.refunded, days_ago=0)
    await _sale(db, buyer, seller, card, 3000, finish="foil", quantity=2, days_ago=0.5)
    await recompute_market_prices(db, NOW)

    detail = (await client.get(f"/v1/cards/{card}")).json()
    assert [(m["finish"], m["language"], m["price_cents"], m["source"]) for m in detail["market_prices"]] == [
        ("nonfoil", "en", 1000, "sales")]

    sales = (await client.get(f"/v1/cards/{card}/sales")).json()
    assert [(s["price_cents"], s["condition"], s["quantity"]) for s in sales] == [
        (3000, "NM", 2), (1100, "NM", 1), (900, "LP", 1), (1000, "NM", 1)]
    assert set(sales[0]) == {"condition", "finish", "language", "quantity", "price_cents", "sold_on"}
    lp = (await client.get(f"/v1/cards/{card}/sales", params={"condition": "LP"})).json()
    assert [s["price_cents"] for s in lp] == [900]
    nonfoil = (await client.get(f"/v1/cards/{card}/sales", params={"finish": "nonfoil", "limit": 2})).json()
    assert [s["price_cents"] for s in nonfoil] == [1100, 900]


async def test_suggested_price(client: AsyncClient, db: AsyncSession, make_user, make_card) -> None:
    buyer, seller = await make_user(), await make_user()
    card = await make_card()
    for _ in range(3):
        await _sale(db, buyer, seller, card, 1000)
    db.add(CardPrice(card_id=card, prices={"usd": "2.50", "usd_foil": "8.00"}))
    await db.flush()
    await recompute_market_prices(db, NOW)

    async def suggest(**params: str) -> tuple[int | None, str | None]:
        resp = await client.get(f"/v1/cards/{card}/suggested-price", params=params)
        assert resp.status_code == 200, resp.text
        return resp.json()["price_cents"], resp.json()["source"]

    assert await suggest() == (1000, "sales")
    assert await suggest(condition="LP") == (900, "sales")
    # No marketplace price for foil: Scryfall's reference price instead.
    assert await suggest(finish="foil", condition="MP") == (600, "reference")
    # Reference prices are for the printing's own language.
    assert await suggest(finish="foil", language="ja") == (None, None)
    assert await suggest(finish="etched") == (None, None)
    assert (await client.get(f"/v1/cards/{uuid.uuid4()}/suggested-price")).status_code == 404
