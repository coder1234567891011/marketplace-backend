"""
Keyset (cursor) pagination ordered newest-first by (created_at, id).

The cursor is an opaque base64 token, so clients can't depend on its contents.
"""
import base64
import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, TypeVar

from fastapi import Query
from sqlalchemy import Select, literal, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from utils.errors import BadRequest

T = TypeVar("T")

LimitParam = Annotated[int, Query(ge=1, le=100)]
CursorParam = Annotated[str | None, Query()]


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps([created_at.isoformat(), str(row_id)])
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        created_at, row_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.fromisoformat(created_at), uuid.UUID(row_id)
    except (ValueError, TypeError) as e:
        raise BadRequest("Invalid cursor") from e


async def paginate(
    session: AsyncSession, stmt: Select[Any], model: Any, limit: int, cursor: str | None
) -> tuple[Sequence[Any], str | None]:
    """Run stmt (a select of `model`) one page at a time. Returns (rows, next_cursor)."""
    if cursor:
        created_at, row_id = decode_cursor(cursor)
        stmt = stmt.where(tuple_(model.created_at, model.id) < tuple_(literal(created_at), literal(row_id)))
    stmt = stmt.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1)
    rows = (await session.scalars(stmt)).all()
    if len(rows) <= limit:
        return rows, None
    rows = rows[:limit]
    return rows, encode_cursor(rows[-1].created_at, rows[-1].id)
