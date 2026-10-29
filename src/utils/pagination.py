"""
Keyset (cursor) pagination. Most lists are ordered newest-first by
(created_at, id) via paginate(); keyset_paginate() takes any order.

The cursor is an opaque base64 token, so clients can't depend on its contents.
"""
import base64
import json
import uuid
from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Annotated, Any, TypeVar

from fastapi import Query
from sqlalchemy import Select, literal, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from utils.errors import BadRequest

T = TypeVar("T")

LimitParam = Annotated[int, Query(ge=1, le=100)]
CursorParam = Annotated[str | None, Query()]

# A sort column and the function that turns its cursor value back into a
# Python value. Every sort column must be NOT NULL.
SortKey = tuple[InstrumentedAttribute[Any], Callable[[str], Any]]


def encode_cursor(*values: datetime | date | uuid.UUID | str) -> str:
    raw = json.dumps([v.isoformat() if isinstance(v, date) else str(v) for v in values])
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str, *parsers: Callable[[str], Any]) -> tuple[Any, ...]:
    try:
        values = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if not isinstance(values, list) or len(values) != len(parsers):
            raise ValueError("wrong number of cursor values")
        return tuple(parse(value) for parse, value in zip(parsers, values, strict=True))
    except (ValueError, TypeError) as e:
        raise BadRequest("Invalid cursor") from e


async def keyset_paginate(
    session: AsyncSession, stmt: Select[Any], keys: Sequence[SortKey], limit: int, cursor: str | None,
    descending: bool = False,
) -> tuple[Sequence[Any], str | None]:
    """Run stmt one page at a time, ordered by keys (all ascending or all
    descending). Returns (rows, next_cursor)."""
    columns = [column for column, _ in keys]
    if cursor:
        after = tuple_(*(literal(v) for v in decode_cursor(cursor, *(parse for _, parse in keys))))
        stmt = stmt.where(tuple_(*columns) < after if descending else tuple_(*columns) > after)
    stmt = stmt.order_by(*(c.desc() if descending else c.asc() for c in columns)).limit(limit + 1)
    rows = (await session.scalars(stmt)).all()
    if len(rows) <= limit:
        return rows, None
    rows = rows[:limit]
    return rows, encode_cursor(*(getattr(rows[-1], column.key) for column in columns))


async def paginate(
    session: AsyncSession, stmt: Select[Any], model: Any, limit: int, cursor: str | None
) -> tuple[Sequence[Any], str | None]:
    """Run stmt (a select of `model`) newest first. Returns (rows, next_cursor)."""
    keys: list[SortKey] = [(model.created_at, datetime.fromisoformat), (model.id, uuid.UUID)]
    return await keyset_paginate(session, stmt, keys, limit, cursor, descending=True)
