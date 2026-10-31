import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from models.database import CardCondition, CardFinish, ScanStatus
from models.object_models.cards import CardRead
from models.object_models.collections import Language

PhotoType = Literal["image/jpeg", "image/png", "image/webp", "image/heic"]


class ScanText(BaseModel):
    """Text the app read from the photo (OCR on the device)."""
    # The bottom-left collector line, e.g. "0150 R\nM21 • EN".
    collector_text: str | None = Field(default=None, max_length=300)
    name_text: str | None = Field(default=None, max_length=200)


class ScanPhoto(BaseModel):
    content_type: PhotoType
    # Exact size of the file; the upload link only accepts a file of this size.
    size_bytes: int = Field(ge=1)


class ScanCreate(ScanText):
    """Start a scan. Include the text if it's already read, to match in one call;
    include photo to get a link to upload it to."""
    photo: ScanPhoto | None = None


class ScanCandidate(BaseModel):
    card: CardRead
    confidence: float
    match: Literal["collector_number", "name"]


class PhotoUpload(BaseModel):
    """PUT the file to url with these headers before expires_at."""
    url: str
    headers: dict[str, str]
    expires_at: datetime


class ScanRead(BaseModel):
    id: uuid.UUID
    status: ScanStatus
    collector_text: str | None
    name_text: str | None
    detected_language: str | None
    detected_foil: bool | None
    # Most likely first.
    candidates: list[ScanCandidate]
    collection_item_id: uuid.UUID | None
    # A temporary link to the photo, if one was uploaded.
    photo_url: str | None
    created_at: datetime


class ScanCreated(ScanRead):
    upload: PhotoUpload | None = None


class ScanConfirm(BaseModel):
    """Add the scanned card to a collection. card_id defaults to the best
    candidate; finish and language default to what the photo showed."""
    collection_id: uuid.UUID
    card_id: uuid.UUID | None = None
    condition: CardCondition | None = None
    finish: CardFinish | None = None
    language: Language | None = None
    quantity: int = Field(default=1, ge=1, le=10_000)
