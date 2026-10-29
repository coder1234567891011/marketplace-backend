import uuid

from fastapi import APIRouter

from models.object_models.cards import CardRead
from services.cards.cards import get_card
from services.persistence.database import SessionDep

router = APIRouter(
    prefix="/cards",
    tags=["cards"],
)


@router.get("/{card_id}")
async def read_card(card_id: uuid.UUID, session: SessionDep) -> CardRead:
    return CardRead.model_validate(await get_card(session, card_id))
