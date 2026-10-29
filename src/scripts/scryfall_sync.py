"""
Sync the card catalog from Scryfall's bulk data.

Run from src/ (the nightly GitHub Actions job does this, see
.github/workflows/scryfall-sync.yml):
    python -m scripts.scryfall_sync                     # download and sync
    python -m scripts.scryfall_sync --file cards.jsonl.gz   # sync a local copy

Uses Scryfall's "default_cards" file: every printing, in English or in its
only printed language. It comes as gzipped JSON Lines (one card per line,
about 80 MB compressed), downloaded to a temporary file and read one card at
a time, so memory stays small. Paper cards only: digital-only
(Arena, MTGO) printings are skipped.

Rows are matched on scryfall_id, so cards.id never changes and listings keep
pointing at the same card. A row is only rewritten when something in it
changed, and prices live in card_prices, so a nightly run with a few price
moves writes little and keeps table bloat down on Supabase's free tier.
"""
import argparse
import asyncio
import gzip
import json
import tempfile
import uuid
from collections.abc import Iterator
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import bindparam, func, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from models.database import Card
from services.persistence.database import create_engine
from utils.settings import settings

BULK_DATA_URL = "https://api.scryfall.com/bulk-data/default-cards"
DEFAULT_USER_AGENT = "MTGMarketplace/1.0"
BATCH_SIZE = 500
IMAGE_SIZES = ("small", "normal")
FACE_FIELDS = ("name", "mana_cost", "type_line", "oracle_text")
# Scryfall-only objects that aren't cards anyone trades.
SKIP_LAYOUTS = {"art_series"}

# Columns the sync owns. Everything except id and last_synced_at.
CATALOG_COLUMNS = (
    "oracle_id", "name", "set_code", "set_name", "collector_number", "lang", "released_at", "rarity",
    "layout", "mana_cost", "cmc", "type_line", "oracle_text", "colors", "color_identity", "finishes",
    "image_uris", "card_faces",
)


# ----------------------------------------------------------------
# Scryfall card → rows
# ----------------------------------------------------------------
def _images(image_uris: dict[str, str] | None) -> dict[str, str] | None:
    if not image_uris:
        return None
    return {size: image_uris[size] for size in IMAGE_SIZES if size in image_uris} or None


def _face(face: dict[str, Any]) -> dict[str, Any]:
    trimmed = {field: face[field] for field in FACE_FIELDS if face.get(field)}
    images = _images(face.get("image_uris"))
    if images:
        trimmed["image_uris"] = images
    return trimmed


def card_rows(card: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]] | None:
    """(cards row, reference prices) for one Scryfall card, or None to skip it."""
    if "paper" not in card.get("games", ()) or card.get("layout") in SKIP_LAYOUTS:
        return None
    faces = card.get("card_faces")
    # Reversible cards keep oracle_id on their faces only.
    oracle_id = card.get("oracle_id") or (faces[0].get("oracle_id") if faces else None)
    if not oracle_id:
        return None
    row = {
        "scryfall_id": uuid.UUID(card["id"]),
        "oracle_id": uuid.UUID(oracle_id),
        "name": card["name"],
        "set_code": card["set"],
        "set_name": card["set_name"],
        "collector_number": card["collector_number"],
        "lang": card["lang"],
        "released_at": date.fromisoformat(card["released_at"]),
        "rarity": card.get("rarity"),
        "layout": card["layout"],
        "mana_cost": card.get("mana_cost"),
        "cmc": card.get("cmc"),
        "type_line": card.get("type_line"),
        "oracle_text": card.get("oracle_text"),
        "colors": card.get("colors"),
        "color_identity": card.get("color_identity", []),
        "finishes": card.get("finishes", []),
        "image_uris": _images(card.get("image_uris")),
        "card_faces": [_face(f) for f in faces] if faces else None,
    }
    prices = {name: value for name, value in (card.get("prices") or {}).items() if value is not None}
    return row, prices


# ----------------------------------------------------------------
# Database
# ----------------------------------------------------------------
# Points an existing row for the same printing (set, collector number,
# language) at Scryfall's id, so the upsert below updates it instead of
# hitting the unique constraint. Covers cards added before the sync existed,
# and Scryfall replacing a printing's id.
_ADOPT_PRINTINGS = text("""
    UPDATE cards c SET scryfall_id = v.scryfall_id
    FROM unnest(CAST(:scryfall_ids AS uuid[]), CAST(:set_codes AS text[]),
                CAST(:collector_numbers AS text[]), CAST(:langs AS text[]))
         AS v(scryfall_id, set_code, collector_number, lang)
    WHERE lower(c.set_code) = v.set_code AND c.collector_number = v.collector_number AND c.lang = v.lang
      AND c.scryfall_id <> v.scryfall_id
      AND NOT EXISTS (SELECT 1 FROM cards taken WHERE taken.scryfall_id = v.scryfall_id)
""")

_UPSERT_PRICES = text("""
    INSERT INTO card_prices (card_id, prices)
    SELECT c.id, v.prices::jsonb
    FROM unnest(CAST(:scryfall_ids AS uuid[]), CAST(:prices AS text[])) AS v(scryfall_id, prices)
    JOIN cards c ON c.scryfall_id = v.scryfall_id
    ON CONFLICT (card_id) DO UPDATE SET prices = excluded.prices, updated_at = now()
    WHERE card_prices.prices IS DISTINCT FROM excluded.prices
""").bindparams(bindparam("scryfall_ids"), bindparam("prices"))


async def upsert_batch(session: AsyncSession, batch: list[tuple[dict[str, Any], dict[str, str]]]) -> tuple[int, int]:
    """Insert or update one batch. Returns (cards written, price rows written)."""
    await session.execute(_ADOPT_PRINTINGS, {
        "scryfall_ids": [str(row["scryfall_id"]) for row, _ in batch],
        "set_codes": [row["set_code"] for row, _ in batch],
        "collector_numbers": [row["collector_number"] for row, _ in batch],
        "langs": [row["lang"] for row, _ in batch],
    })
    stmt = insert(Card).values([row for row, _ in batch])
    new = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        index_elements=[Card.scryfall_id],
        set_={**{column: new[column] for column in CATALOG_COLUMNS}, "last_synced_at": func.now()},
        # Skip rows that haven't changed, so they aren't rewritten.
        where=tuple_(*(Card.__table__.c[c] for c in CATALOG_COLUMNS)).is_distinct_from(
            tuple_(*(new[c] for c in CATALOG_COLUMNS))),
    )
    cards_written = (await session.execute(stmt)).rowcount  # type: ignore[attr-defined]
    prices_written = (await session.execute(_UPSERT_PRICES, {
        "scryfall_ids": [str(row["scryfall_id"]) for row, _ in batch],
        "prices": [json.dumps(prices, sort_keys=True) for _, prices in batch],
    })).rowcount  # type: ignore[attr-defined]
    return cards_written, prices_written


# ----------------------------------------------------------------
# Download and run
# ----------------------------------------------------------------
async def download(destination: Path) -> None:
    headers = {"User-Agent": settings.scryfall_user_agent or DEFAULT_USER_AGENT, "Accept": "application/json"}
    async with httpx.AsyncClient(headers=headers, timeout=120, follow_redirects=True) as http:
        meta = (await http.get(BULK_DATA_URL)).raise_for_status().json()
        url = meta.get("jsonl_download_uri")
        if not url:
            raise SystemExit(f"Scryfall's bulk-data response has no jsonl_download_uri; fields: {sorted(meta)}")
        print(f"Downloading {url} (updated {meta['updated_at']})", flush=True)
        async with http.stream("GET", url) as response:
            response.raise_for_status()
            with destination.open("wb") as out:
                async for chunk in response.aiter_bytes(1 << 20):
                    out.write(chunk)


def read_cards(path: Path) -> Iterator[dict[str, Any]]:
    """Cards from a JSON Lines file (gzipped or not), or a small JSON array."""
    with ExitStack() as stack:
        raw = stack.enter_context(path.open("rb"))
        if raw.read(2) == b"\x1f\x8b":  # gzip magic number
            raw.seek(0)
            f = stack.enter_context(gzip.open(raw, "rt", encoding="utf-8"))
        else:
            raw.seek(0)
            f = stack.enter_context(open(path, encoding="utf-8"))
        first = f.read(1)
        while first.isspace():
            first = f.read(1)
        if first == "[":
            # A JSON array (e.g. saved from the search API): small files only.
            yield from json.loads(first + f.read())
            return
        yield json.loads(first + f.readline())
        for line in f:
            if line.strip():
                yield json.loads(line)


async def describe_conflict(session: AsyncSession, row: dict[str, Any]) -> str:
    """The existing rows a card clashes with, for the end-of-run report."""
    existing = (await session.execute(
        select(Card.id, Card.scryfall_id, Card.name, Card.set_code, Card.collector_number, Card.lang).where(
            (Card.scryfall_id == row["scryfall_id"])
            | ((func.lower(Card.set_code) == row["set_code"]) & (Card.collector_number == row["collector_number"])
               & (Card.lang == row["lang"]))))).all()
    found = "; ".join(f"cards.id {e.id} (scryfall_id {e.scryfall_id}, {e.name}, {e.set_code} "
                      f"#{e.collector_number} {e.lang})" for e in existing)
    return (f"{row['name']} ({row['set_code']} #{row['collector_number']} {row['lang']}, "
            f"scryfall_id {row['scryfall_id']}) clashes with: {found or 'nothing found'}")


async def sync(path: Path, sessionmaker: async_sessionmaker[AsyncSession]) -> list[str]:
    """Sync every card in path. Returns the cards that clashed with existing
    rows and were skipped."""
    seen = skipped = cards_written = prices_written = batches = 0
    batch: list[tuple[dict[str, Any], dict[str, str]]] = []
    conflicts: list[str] = []

    async def write(rows: list[tuple[dict[str, Any], dict[str, str]]]) -> None:
        nonlocal cards_written, prices_written
        # One transaction per batch: a failure keeps earlier batches, and a
        # re-run picks up where it left off.
        async with sessionmaker() as session, session.begin():
            written = await upsert_batch(session, rows)
        cards_written += written[0]
        prices_written += written[1]

    async def flush() -> None:
        nonlocal batches
        try:
            await write(batch)
        except IntegrityError:
            # Some card clashes with a row that the sync can't safely merge
            # (e.g. two existing rows for one printing). Write the batch one
            # card at a time and report the ones that fail.
            for rows in batch:
                try:
                    await write([rows])
                except IntegrityError:
                    async with sessionmaker() as session:
                        conflicts.append(await describe_conflict(session, rows[0]))
        batches += 1
        batch.clear()
        if batches % 40 == 0:
            print(f"  {seen:,} cards read", flush=True)

    for card in read_cards(path):
        seen += 1
        rows = card_rows(card)
        if rows is None:
            skipped += 1
            continue
        batch.append(rows)
        if len(batch) >= BATCH_SIZE:
            await flush()
    if batch:
        await flush()
    print(f"Done: {seen:,} cards read, {skipped:,} skipped (digital-only or not a card), "
          f"{cards_written:,} added or changed, {prices_written:,} price updates")
    return conflicts


def report(conflicts: list[str]) -> None:
    if not conflicts:
        return
    print(f"\n{len(conflicts)} card(s) were not synced: an existing row already uses this printing "
          "or this Scryfall id. Delete the duplicate row (or move its listings and collection items "
          "to the other row), then run the sync again:")
    for conflict in conflicts:
        print(f"  - {conflict}")
    raise SystemExit(1)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Sync the card catalog from Scryfall.")
    parser.add_argument("--file", type=Path, help="sync from a local default-cards .jsonl.gz (or .jsonl) file")
    args = parser.parse_args()
    if not settings.database_url:
        raise SystemExit("DATABASE_URL is not configured")

    engine = create_engine(settings.database_url, pool_size=1, max_overflow=0)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        if args.file:
            report(await sync(args.file, sessionmaker))
            return
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "default-cards.jsonl.gz"
            await download(path)
            report(await sync(path, sessionmaker))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
