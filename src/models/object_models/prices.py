from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

from models.database import CardCondition, CardFinish, PriceSource
from models.model import _ORMBase


class MarketPriceRead(_ORMBase):
    """The marketplace price of a card in NM condition, for one finish and language."""
    finish: CardFinish
    language: str
    price_cents: int
    # sales: the median of recent sales; listings: no sales yet, the cheapest NM listing.
    source: PriceSource
    sales_count: int
    computed_at: datetime


class SaleRead(BaseModel):
    """A past sale of a card. Who bought and sold it isn't shown."""
    condition: CardCondition
    finish: CardFinish
    language: str
    quantity: int
    price_cents: int
    sold_on: date


class PricePoint(_ORMBase):
    """The NM market price from this day until the next point."""
    day: date
    price_cents: int
    source: PriceSource


class SuggestedPrice(BaseModel):
    """A starting price for a new listing, scaled to its condition. Null when
    there's nothing to go on."""
    price_cents: int | None
    # reference: no marketplace data yet; scaled from Scryfall's TCGplayer price.
    source: Literal["sales", "listings", "reference"] | None
    sales_count: int = 0
