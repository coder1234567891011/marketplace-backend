import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, status

from auth.auth import AuthUser
from models.database import ScanStatus
from models.model import Page
from models.object_models.scans import ScanConfirm, ScanCreate, ScanCreated, ScanRead, ScanText
from services.persistence.database import SessionDep
from services.scans import scans
from utils import limits
from utils.pagination import CursorParam, LimitParam

router = APIRouter(
    prefix="/scans",
    tags=["scans"],
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[limits.SCANS])
async def start_scan(request: ScanCreate, current: AuthUser, session: SessionDep) -> ScanCreated:
    """Start scanning a card. With photo, returns a link to upload it to;
    with the text read from it, also returns the matching cards."""
    return await scans.create_scan(session, current, request, _now())


@router.get("")
async def list_scans(
    current: AuthUser, session: SessionDep, status: ScanStatus | None = None,
    limit: LimitParam = 50, cursor: CursorParam = None,
) -> Page[ScanRead]:
    """The caller's scans, newest first."""
    rows, next_cursor = await scans.list_scans(session, current, status, limit, cursor)
    return Page(items=await scans.to_reads(session, rows, _now()), next_cursor=next_cursor)


@router.get("/{scan_id}")
async def read_scan(scan_id: uuid.UUID, current: AuthUser, session: SessionDep) -> ScanRead:
    [read] = await scans.to_reads(session, [await scans.get_own_scan(session, current, scan_id)], _now())
    return read


@router.post("/{scan_id}/recognize", dependencies=[limits.SCANS])
async def recognize(scan_id: uuid.UUID, request: ScanText, current: AuthUser, session: SessionDep) -> ScanRead:
    """Match the text read from the card against the catalog."""
    [read] = await scans.to_reads(session, [await scans.recognize_scan(session, current, scan_id, request)], _now())
    return read


@router.post("/{scan_id}/confirm", dependencies=[limits.SCANS])
async def confirm(scan_id: uuid.UUID, request: ScanConfirm, current: AuthUser, session: SessionDep) -> ScanRead:
    """Add the scanned card to a collection."""
    [read] = await scans.to_reads(session, [await scans.confirm_scan(session, current, scan_id, request)], _now())
    return read


@router.delete("/{scan_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[limits.SCANS])
async def delete(scan_id: uuid.UUID, current: AuthUser, session: SessionDep) -> None:
    await scans.delete_scan(session, current, scan_id)
