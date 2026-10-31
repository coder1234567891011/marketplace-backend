import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Query

from models.database import CardCondition, CardFinish
from models.model import Page
from models.object_models.cards import CardDetail, CardName, CardWithPrices
from models.object_models.prices import MarketPriceRead, PricePoint, SaleRead, SuggestedPrice
from services.cards import cards
from services.persistence.database import SessionDep
from services.prices import prices
from utils.pagination import CursorParam, LimitParam

LanguageParam = Annotated[str | None, Query(max_length=5, description="Language code, e.g. en, ja")]

router = APIRouter(
    prefix="/cards",
    tags=["cards"],
)


@router.get("/autocomplete")
async def autocomplete(
    session: SessionDep,
    q: Annotated[str, Query(min_length=2, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> list[CardName]:
    """Card names for a search box: substring and typo-tolerant, one per card."""
    return [CardName(oracle_id=oracle_id, name=name) for oracle_id, name in await cards.autocomplete(session, q, limit)]


@router.get("/search")
async def search(
    session: SessionDep,
    q: Annotated[str | None, Query(max_length=100, description="Part of the card name")] = None,
    set: Annotated[str | None, Query(max_length=10, description="Set code, e.g. m10")] = None,
    rarity: Annotated[str | None, Query(max_length=20, description="common, uncommon, rare, mythic, …")] = None,
    type: Annotated[str | None, Query(max_length=100, description="Part of the type line, e.g. Creature")] = None,
    colors: Annotated[str | None, Query(
        pattern="^[WUBRGwubrg]{1,5}$", description="Color identity includes all of these, e.g. UR")] = None,
    finish: cards.Finish | None = None,
    lang: Annotated[str | None, Query(max_length=5, description="Language code, e.g. en, ja")] = None,
    limit: LimitParam = 50,
    cursor: CursorParam = None,
) -> Page[CardDetail]:
    """Printings matching every given filter, sorted by name."""
    rows, next_cursor = await cards.search_cards(
        session, limit=limit, cursor=cursor, name=q, set_code=set, rarity=rarity, type_line=type,
        colors=colors, finish=finish, lang=lang)
    return Page(items=[CardDetail.model_validate(c) for c in rows], next_cursor=next_cursor)


@router.get("/oracle/{oracle_id}/printings")
async def printings(
    oracle_id: uuid.UUID, session: SessionDep, limit: LimitParam = 50, cursor: CursorParam = None
) -> Page[CardDetail]:
    """Every printing of one card (all sets and languages), newest first."""
    rows, next_cursor = await cards.list_printings(session, oracle_id, limit, cursor)
    return Page(items=[CardDetail.model_validate(c) for c in rows], next_cursor=next_cursor)


@router.get("/{card_id}")
async def read_card(card_id: uuid.UUID, session: SessionDep) -> CardWithPrices:
    """One printing, with its marketplace prices and Scryfall's reference prices."""
    card = CardWithPrices.model_validate(await cards.get_card(session, card_id))
    card.market_prices = [MarketPriceRead.model_validate(m) for m in await prices.market_prices(session, card_id)]
    return card


@router.get("/{card_id}/sales")
async def recent_sales(
    card_id: uuid.UUID, session: SessionDep,
    finish: CardFinish | None = None, language: LanguageParam = None, condition: CardCondition | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[SaleRead]:
    """Recent sales on the marketplace, newest first."""
    return await prices.recent_sales(session, card_id, limit, finish, language, condition)


@router.get("/{card_id}/price-history")
async def price_history(
    card_id: uuid.UUID, session: SessionDep, finish: CardFinish = CardFinish.nonfoil,
    language: Annotated[str, Query(max_length=5)] = "en", days: Annotated[int, Query(ge=7, le=730)] = 90,
) -> list[PricePoint]:
    """NM market price changes over the last days. Each point holds until the
    next; the first is the price when the period began."""
    points = await prices.price_history(session, card_id, finish, language, days, datetime.now(timezone.utc).date())
    return [PricePoint.model_validate(p) for p in points]


@router.get("/{card_id}/suggested-price")
async def suggested_price(
    card_id: uuid.UUID, session: SessionDep, condition: CardCondition = CardCondition.NM,
    finish: CardFinish = CardFinish.nonfoil, language: Annotated[str, Query(max_length=5)] = "en",
) -> SuggestedPrice:
    """A starting price for a listing of this card."""
    return await prices.suggested_price(session, card_id, condition, finish, language)
