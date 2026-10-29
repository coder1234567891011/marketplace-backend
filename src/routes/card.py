import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from models.model import Page
from models.object_models.cards import CardDetail, CardName
from services.cards import cards
from services.persistence.database import SessionDep
from utils.pagination import CursorParam, LimitParam

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
async def read_card(card_id: uuid.UUID, session: SessionDep) -> CardDetail:
    return CardDetail.model_validate(await cards.get_card(session, card_id))
