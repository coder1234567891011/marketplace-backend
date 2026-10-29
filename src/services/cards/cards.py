import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import Card
from utils.errors import NotFound


async def get_card(session: AsyncSession, card_id: uuid.UUID) -> Card:
    card = await session.get(Card, card_id)
    if card is None:
        raise NotFound("Card not found")
    return card


async def ensure_cards_exist(session: AsyncSession, card_ids: Iterable[uuid.UUID]) -> None:
    wanted = set(card_ids)
    found = set((await session.scalars(select(Card.id).where(Card.id.in_(wanted)))).all())
    missing = wanted - found
    if missing:
        raise NotFound(f"Card not found: {', '.join(sorted(str(m) for m in missing))}")
