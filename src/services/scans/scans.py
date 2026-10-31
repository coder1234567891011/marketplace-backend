"""
Scanning cards into a collection: create a scan (optionally with a photo
upload link), send the text read from the photo, then confirm the match,
which adds the card to a collection.
"""
import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.auth import CurrentUser
from models.database import Card, CardFinish, CollectionItem, RecognitionStatus, Scan, ScanStatus
from models.object_models.cards import CardRead
from models.object_models.scans import (
    PhotoUpload,
    ScanCandidate,
    ScanConfirm,
    ScanCreate,
    ScanCreated,
    ScanRead,
    ScanText,
)
from services.cards.cards import load_cards
from services.collections.collections import check_finish, get_own_collection
from services.scans.matching import match_card, parse_collector_line
from services.storage import storage
from services.user.user import require_profile
from utils.errors import BadRequest, Conflict, NotFound
from utils.pagination import SortKey, keyset_paginate
from utils.settings import settings

EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/heic": "heic"}


async def to_reads(session: AsyncSession, scans: Sequence[Scan], now: datetime) -> list[ScanRead]:
    """API views of scans, with their candidate cards loaded in one query."""
    ids = {uuid.UUID(c["card_id"]) for s in scans for c in s.candidates}
    cards = {c.id: c for c in await session.scalars(select(Card).where(Card.id.in_(ids)))} if ids else {}
    return [ScanRead(
        id=s.id, status=s.status, collector_text=s.collector_text, name_text=s.name_text,
        detected_language=s.detected_language, detected_foil=s.detected_foil,
        candidates=[ScanCandidate(card=CardRead.model_validate(cards[uuid.UUID(c["card_id"])]),
                                  confidence=c["confidence"], match=c["match"])
                    for c in s.candidates if uuid.UUID(c["card_id"]) in cards],
        collection_item_id=s.collection_item_id,
        photo_url=storage.download_url(s.photo_key, now) if s.photo_key else None,
        created_at=s.created_at,
    ) for s in scans]


async def _recognize(session: AsyncSession, scan: Scan, text: ScanText) -> None:
    scan.collector_text, scan.name_text = text.collector_text, text.name_text
    candidates = await match_card(session, text.collector_text, text.name_text)
    scan.candidates = [{"card_id": str(c.card.id), "confidence": c.confidence, "match": c.match}
                       for c in candidates]
    scan.status = ScanStatus.matched if candidates else ScanStatus.unmatched
    line = parse_collector_line(text.collector_text) if text.collector_text else None
    scan.detected_language, scan.detected_foil = (line.language, line.foil) if line else (None, None)
    await session.flush()


async def create_scan(session: AsyncSession, current: CurrentUser, request: ScanCreate, now: datetime) -> ScanCreated:
    await require_profile(session, current)
    scan = Scan(id=uuid.uuid4(), user_id=current.id)
    upload = None
    if request.photo:
        if request.photo.size_bytes > settings.scan_photo_max_bytes:
            raise BadRequest(f"Photos can be up to {settings.scan_photo_max_bytes // 1_000_000} MB")
        scan.photo_key = f"scans/{current.id}/{scan.id}.{EXTENSIONS[request.photo.content_type]}"
        signed = storage.upload(scan.photo_key, request.photo.content_type, request.photo.size_bytes, now)
        upload = PhotoUpload(url=signed.url, headers=signed.headers, expires_at=signed.expires_at)
    session.add(scan)
    await session.flush()
    if request.collector_text or request.name_text:
        await _recognize(session, scan, request)
    [read] = await to_reads(session, [scan], now)
    return ScanCreated(**read.model_dump(), upload=upload)


async def get_own_scan(session: AsyncSession, current: CurrentUser, scan_id: uuid.UUID, lock: bool = False) -> Scan:
    stmt = select(Scan).where(Scan.id == scan_id, Scan.user_id == current.id)
    if lock:
        stmt = stmt.with_for_update()
    scan = await session.scalar(stmt)
    if scan is None:
        raise NotFound("Scan not found")
    return scan


async def list_scans(
    session: AsyncSession, current: CurrentUser, status: ScanStatus | None, limit: int, cursor: str | None,
) -> tuple[Sequence[Scan], str | None]:
    """The caller's scans, newest first."""
    stmt = select(Scan).where(Scan.user_id == current.id)
    if status:
        stmt = stmt.where(Scan.status == status)
    keys: list[SortKey] = [(Scan.created_at, datetime.fromisoformat), (Scan.id, uuid.UUID)]
    return await keyset_paginate(session, stmt, keys, limit, cursor, descending=True)


async def recognize_scan(session: AsyncSession, current: CurrentUser, scan_id: uuid.UUID, text: ScanText) -> Scan:
    """Match (or re-match, e.g. after a better photo) the text read from the card."""
    if not text.collector_text and not text.name_text:
        raise BadRequest("Send the collector line, the name, or both")
    scan = await get_own_scan(session, current, scan_id, lock=True)
    if scan.status == ScanStatus.confirmed:
        raise Conflict("This scan was already added to a collection")
    await _recognize(session, scan, text)
    return scan


async def confirm_scan(session: AsyncSession, current: CurrentUser, scan_id: uuid.UUID, request: ScanConfirm) -> Scan:
    """Add the scanned card to a collection. Copies already in it (same
    printing, condition, finish and language) get their quantity raised."""
    scan = await get_own_scan(session, current, scan_id, lock=True)
    if scan.status == ScanStatus.confirmed:
        raise Conflict("This scan was already added to a collection")
    await get_own_collection(session, current, request.collection_id)
    card_id = request.card_id
    if card_id is None:
        if not scan.candidates:
            raise BadRequest("Nothing matched this scan; choose the card")
        card_id = uuid.UUID(scan.candidates[0]["card_id"])
    card = (await load_cards(session, [card_id]))[card_id]
    finish = request.finish or (CardFinish.foil if scan.detected_foil and "foil" in card.finishes
                                else CardFinish.nonfoil)
    check_finish(card, finish)
    language = request.language or scan.detected_language or card.lang

    item = await session.scalar(select(CollectionItem).with_for_update().where(
        CollectionItem.collection_id == request.collection_id, CollectionItem.card_id == card.id,
        CollectionItem.finish == finish, CollectionItem.language == language,
        CollectionItem.condition.is_(None) if request.condition is None
        else CollectionItem.condition == request.condition))
    if item is not None:
        item.quantity += request.quantity
    else:
        matched = next((c for c in scan.candidates if c["card_id"] == str(card.id)), None)
        item = CollectionItem(
            collection_id=request.collection_id, card_id=card.id, condition=request.condition, finish=finish,
            language=language, quantity=request.quantity,
            recognition_status=RecognitionStatus.confirmed if matched else RecognitionStatus.manual,
            recognition_confidence=matched["confidence"] if matched else None)
        session.add(item)
    await session.flush()
    scan.status, scan.collection_item_id = ScanStatus.confirmed, item.id
    await session.flush()
    return scan


async def delete_scan(session: AsyncSession, current: CurrentUser, scan_id: uuid.UUID) -> None:
    """Forget a scan. Its photo is removed by the bucket's lifecycle rule
    (README); a confirmed card stays in the collection."""
    scan = await get_own_scan(session, current, scan_id, lock=True)
    await session.delete(scan)
    await session.flush()
