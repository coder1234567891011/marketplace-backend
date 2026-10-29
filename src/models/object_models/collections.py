# ----------------------------------------------------------------
# COLLECTIONS
# ----------------------------------------------------------------
import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from models.database import CardCondition, CardFinish, RecognitionStatus
from models.model import _ORMBase
from models.object_models.cards import CardRead

# Scryfall language codes: en, ja, de, zhs, …
Language = Annotated[str, Field(pattern=r"^[a-z]{2,3}$")]


class CollectionCreate(BaseModel):
    name: str = "My Collection"
    is_public: bool = False


class CollectionUpdate(BaseModel):
    name: str | None = None
    is_public: bool | None = None


class CollectionRead(_ORMBase, CollectionCreate):
    id: uuid.UUID
    user_id: uuid.UUID
    created_at: datetime
    updated_at: datetime



# ----------------------------------------------------------------
# COLLECTION ITEMS
# ----------------------------------------------------------------
class CollectionItemCreate(BaseModel):
    card_id: uuid.UUID
    condition: CardCondition | None = None
    finish: CardFinish = CardFinish.nonfoil
    language: Language = "en"
    quantity: int = Field(default=1, ge=1, le=10_000)
    scan_image_url: str | None = None


class CollectionItemUpdate(BaseModel):
    condition: CardCondition | None = None
    finish: CardFinish | None = None
    language: Language | None = None
    quantity: int | None = Field(default=None, ge=1, le=10_000)


class CollectionItemRead(_ORMBase):
    id: uuid.UUID
    collection_id: uuid.UUID
    card: CardRead
    condition: CardCondition | None
    finish: CardFinish
    language: str
    quantity: int
    # Copies reserved by active or pending listings; quantity - quantity_listed
    # are free to list.
    quantity_listed: int = 0
    recognition_confidence: float | None
    recognition_status: RecognitionStatus
    created_at: datetime


# ----------------------------------------------------------------
# IMPORT
# ----------------------------------------------------------------
class CollectionImport(BaseModel):
    """A pasted decklist (Arena, MTGO or plain "4 Lightning Bolt" lines) or a
    CSV export with a header row (Moxfield, ManaBox, Deckbox, …)."""
    format: Literal["decklist", "csv"]
    text: str = Field(min_length=1, max_length=200_000)
    # Used where a line doesn't say.
    condition: CardCondition | None = None
    language: Language = "en"


class ImportedLine(BaseModel):
    line: int
    text: str
    card: CardRead
    quantity: int
    condition: CardCondition | None
    finish: CardFinish
    language: str
    # Set when the line didn't pin down one printing, e.g. no set given.
    note: str | None = None


class ImportProblem(BaseModel):
    line: int
    text: str
    error: str


class ImportResult(BaseModel):
    dry_run: bool
    matched: list[ImportedLine]
    unmatched: list[ImportProblem]
    # Rows added to the collection, and existing rows whose quantity went up
    # (same card, finish, condition and language). Both 0 on a dry run.
    items_created: int
    items_updated: int
