import uuid
from collections.abc import Iterable, Sequence
from datetime import date
from typing import Literal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from models.database import Card
from utils.errors import NotFound
from utils.pagination import SortKey, keyset_paginate

Finish = Literal["nonfoil", "foil", "etched"]


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def get_card(session: AsyncSession, card_id: uuid.UUID) -> Card:
    card = await session.scalar(select(Card).options(joinedload(Card.prices)).where(Card.id == card_id))
    if card is None:
        raise NotFound("Card not found")
    return card


async def ensure_cards_exist(session: AsyncSession, card_ids: Iterable[uuid.UUID]) -> None:
    wanted = set(card_ids)
    found = set((await session.scalars(select(Card.id).where(Card.id.in_(wanted)))).all())
    missing = wanted - found
    if missing:
        raise NotFound(f"Card not found: {', '.join(sorted(str(m) for m in missing))}")


async def autocomplete(session: AsyncSession, query: str, limit: int) -> Sequence[tuple[uuid.UUID, str]]:
    """Card names containing query, or close to it (typos), one per card.
    Names starting with query come first, then the closest matches."""
    query = query.strip()
    pattern = _like_escape(query)
    stmt = (
        select(Card.oracle_id, Card.name)
        # % is pg_trgm's "similar to" operator; both conditions use idx_cards_name_trgm.
        .where(or_(Card.name.ilike(f"%{pattern}%"), Card.name.op("%")(query)))
        .group_by(Card.oracle_id, Card.name)
        .order_by(Card.name.ilike(f"{pattern}%").desc(), func.similarity(Card.name, query).desc(), Card.name)
        .limit(limit)
    )
    return [(row.oracle_id, row.name) for row in await session.execute(stmt)]


async def search_cards(
    session: AsyncSession, *, limit: int, cursor: str | None, name: str | None = None,
    set_code: str | None = None, rarity: str | None = None, type_line: str | None = None,
    colors: str | None = None, finish: Finish | None = None, lang: str | None = None,
) -> tuple[Sequence[Card], str | None]:
    """Printings matching every given filter, sorted by name."""
    stmt = select(Card).options(joinedload(Card.prices))
    if name:
        stmt = stmt.where(Card.name.ilike(f"%{_like_escape(name.strip())}%"))
    if set_code:
        stmt = stmt.where(Card.set_code == set_code.lower())
    if rarity:
        stmt = stmt.where(Card.rarity == rarity.lower())
    if type_line:
        stmt = stmt.where(Card.type_line.ilike(f"%{_like_escape(type_line.strip())}%"))
    if colors:
        # Color identity includes every given color, e.g. "UR" → blue and red.
        stmt = stmt.where(Card.color_identity.contains(sorted(set(colors.upper()))))
    if finish:
        stmt = stmt.where(Card.finishes.contains([finish]))
    if lang:
        stmt = stmt.where(Card.lang == lang.lower())
    keys: list[SortKey] = [(Card.name, str), (Card.id, uuid.UUID)]
    return await keyset_paginate(session, stmt, keys, limit, cursor)


async def list_printings(
    session: AsyncSession, oracle_id: uuid.UUID, limit: int, cursor: str | None
) -> tuple[Sequence[Card], str | None]:
    """Every printing of one card, newest first."""
    exists = await session.scalar(select(Card.id).where(Card.oracle_id == oracle_id).limit(1))
    if exists is None:
        raise NotFound("Card not found")
    stmt = select(Card).options(joinedload(Card.prices)).where(Card.oracle_id == oracle_id)
    keys: list[SortKey] = [(Card.released_at, date.fromisoformat), (Card.id, uuid.UUID)]
    return await keyset_paginate(session, stmt, keys, limit, cursor, descending=True)
