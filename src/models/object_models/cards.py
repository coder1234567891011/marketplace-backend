import uuid

from models.model import _ORMBase


# ----------------------------------------------------------------
# CARDS
# ----------------------------------------------------------------
class CardRead(_ORMBase):
    id: uuid.UUID
    name: str
    set_code: str
    collector_number: str
    rarity: str | None
    image_url: str | None
    is_foil_available: bool

