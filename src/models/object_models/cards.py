import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator

from models.model import _ORMBase


# ----------------------------------------------------------------
# CARDS
# ----------------------------------------------------------------
class CardFace(BaseModel):
    name: str
    mana_cost: str | None = None
    type_line: str | None = None
    oracle_text: str | None = None
    image_uris: dict[str, str] | None = None


class CardRead(_ORMBase):
    """One printing of a card. Embedded in listings and collection items."""
    id: uuid.UUID
    oracle_id: uuid.UUID
    name: str
    set_code: str
    set_name: str
    set_type: str | None = None
    collector_number: str
    lang: str
    released_at: date
    rarity: str | None
    promo: bool = False
    layout: str
    mana_cost: str | None
    cmc: float | None
    type_line: str | None
    oracle_text: str | None
    colors: list[str] | None
    color_identity: list[str]
    finishes: list[str]
    # Keys: small, normal. Double-faced cards have images per face instead.
    image_uris: dict[str, str] | None
    card_faces: list[CardFace] | None


class CardDetail(CardRead):
    """A printing plus Scryfall's market prices, which are only an outside
    reference: marketplace prices come from sales on this platform."""
    reference_prices: dict[str, str] | None = Field(default=None, validation_alias="prices")

    @field_validator("reference_prices", mode="before")
    @classmethod
    def _from_card_price(cls, value: Any) -> Any:
        # Read from Card.prices, a CardPrice row (or None).
        return getattr(value, "prices", value)


class CardName(BaseModel):
    """An autocomplete suggestion: one card, across all its printings."""
    oracle_id: uuid.UUID
    name: str
