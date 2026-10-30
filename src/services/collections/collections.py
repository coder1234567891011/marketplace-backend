import uuid
from collections.abc import Iterable, Sequence
from datetime import date

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth.auth import CurrentUser
from models.database import Card, CardFinish, Collection, CollectionItem, Listing, ListingStatus
from models.object_models.cards import CardRead
from models.object_models.collections import (
    CollectionCreate,
    CollectionImport,
    CollectionItemCreate,
    CollectionItemUpdate,
    CollectionUpdate,
    ImportedLine,
    ImportProblem,
    ImportResult,
)
from services.cards.cards import load_cards
from services.collections import importer
from services.user.user import require_profile
from utils.errors import BadRequest, Conflict, NotFound
from utils.pagination import paginate

# Listings in these states hold copies of their collection item.
RESERVING = (ListingStatus.active, ListingStatus.pending_sale)


def check_finish(card: Card, finish: CardFinish) -> None:
    # Cards synced from Scryfall list their finishes; placeholder rows don't.
    if card.finishes and finish.value not in card.finishes:
        raise BadRequest(f"{card.name} ({card.set_code.upper()}) isn't printed in {finish.value}; "
                         f"it comes in {', '.join(card.finishes)}")


async def listed_quantities(session: AsyncSession, item_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Copies of each collection item held by active or pending listings,
    including copies in buyers' open checkouts."""
    ids = list(item_ids)
    if not ids:
        return {}
    stmt = (
        select(Listing.collection_item_id, func.sum(Listing.quantity_available + Listing.quantity_reserved))
        .where(Listing.collection_item_id.in_(ids), Listing.status.in_(RESERVING))
        .group_by(Listing.collection_item_id)
    )
    return {item_id: int(total) for item_id, total in await session.execute(stmt)}


async def _release_finished_listings(session: AsyncSession, item_ids: list[uuid.UUID]) -> None:
    """Before deleting collection items: refuse if they're listed, and unlink
    sold or removed listings (kept for order history) so the delete can go ahead."""
    if not item_ids:
        return
    if await listed_quantities(session, item_ids):
        raise Conflict("Some of these cards are listed for sale; remove those listings first")
    await session.execute(update(Listing).where(Listing.collection_item_id.in_(item_ids))
                          .values(collection_item_id=None))


def _is_owner(collection: Collection, current: CurrentUser | None) -> bool:
    return current is not None and collection.user_id == current.id


async def get_user_collections(
    session: AsyncSession, current: CurrentUser | None, user_id: uuid.UUID
) -> Sequence[Collection]:
    stmt = select(Collection).where(Collection.user_id == user_id).order_by(Collection.created_at)
    if current is None or current.id != user_id:
        stmt = stmt.where(Collection.is_public.is_(True))
    return (await session.scalars(stmt)).all()


async def get_collection(
    session: AsyncSession, current: CurrentUser | None, collection_id: uuid.UUID
) -> Collection:
    """A collection the caller may view: their own, or anyone's public one."""
    collection = await session.get(Collection, collection_id)
    if collection is None or not (collection.is_public or _is_owner(collection, current)):
        raise NotFound("Collection not found")
    return collection


async def get_own_collection(session: AsyncSession, current: CurrentUser, collection_id: uuid.UUID) -> Collection:
    collection = await session.get(Collection, collection_id)
    if collection is None or not _is_owner(collection, current):
        raise NotFound("Collection not found")
    return collection


async def create_collection(session: AsyncSession, current: CurrentUser, request: CollectionCreate) -> Collection:
    await require_profile(session, current)
    collection = Collection(user_id=current.id, **request.model_dump())
    session.add(collection)
    await session.flush()
    return collection


async def update_collection(
    session: AsyncSession, current: CurrentUser, collection_id: uuid.UUID, request: CollectionUpdate
) -> Collection:
    collection = await get_own_collection(session, current, collection_id)
    for field, value in request.model_dump(exclude_unset=True).items():
        setattr(collection, field, value)
    await session.flush()
    return collection


async def delete_collection(session: AsyncSession, current: CurrentUser, collection_id: uuid.UUID) -> None:
    collection = await get_own_collection(session, current, collection_id)
    item_ids = list((await session.scalars(
        select(CollectionItem.id).where(CollectionItem.collection_id == collection_id))).all())
    await _release_finished_listings(session, item_ids)
    await session.delete(collection)
    await session.flush()


# ----------------------------------------------------------------
# COLLECTION ITEMS
# ----------------------------------------------------------------
async def get_collection_items(
    session: AsyncSession, current: CurrentUser | None, collection_id: uuid.UUID, limit: int, cursor: str | None
) -> tuple[Sequence[CollectionItem], str | None]:
    await get_collection(session, current, collection_id)
    stmt = (
        select(CollectionItem)
        .where(CollectionItem.collection_id == collection_id)
        .options(selectinload(CollectionItem.card))
    )
    return await paginate(session, stmt, CollectionItem, limit, cursor)


async def _load_item(session: AsyncSession, item_id: uuid.UUID, lock: bool = False) -> CollectionItem | None:
    stmt = (
        select(CollectionItem)
        .where(CollectionItem.id == item_id)
        .options(selectinload(CollectionItem.card), selectinload(CollectionItem.collection))
    )
    if lock:
        # Serializes changes to one item's quantity against listings reserving it.
        stmt = stmt.with_for_update(of=CollectionItem)
    return await session.scalar(stmt)


async def get_collection_item(
    session: AsyncSession, current: CurrentUser | None, item_id: uuid.UUID
) -> CollectionItem:
    item = await _load_item(session, item_id)
    if item is None or not (item.collection.is_public or _is_owner(item.collection, current)):
        raise NotFound("Collection item not found")
    return item


async def get_own_collection_item(
    session: AsyncSession, current: CurrentUser, item_id: uuid.UUID, lock: bool = False
) -> CollectionItem:
    item = await _load_item(session, item_id, lock)
    if item is None or not _is_owner(item.collection, current):
        raise NotFound("Collection item not found")
    return item


async def create_collection_items(
    session: AsyncSession, current: CurrentUser, collection_id: uuid.UUID, requests: list[CollectionItemCreate]
) -> list[CollectionItem]:
    await get_own_collection(session, current, collection_id)
    cards = await load_cards(session, (r.card_id for r in requests))
    for r in requests:
        check_finish(cards[r.card_id], r.finish)
    items = [CollectionItem(collection_id=collection_id, **r.model_dump()) for r in requests]
    session.add_all(items)
    await session.flush()
    ids = [i.id for i in items]
    stmt = select(CollectionItem).where(CollectionItem.id.in_(ids)).options(selectinload(CollectionItem.card))
    loaded = {i.id: i for i in (await session.scalars(stmt)).all()}
    return [loaded[i] for i in ids]


async def update_collection_item(
    session: AsyncSession, current: CurrentUser, item_id: uuid.UUID, request: CollectionItemUpdate
) -> CollectionItem:
    item = await get_own_collection_item(session, current, item_id, lock=True)
    # condition may be cleared with null; the other fields can't be.
    changes = {f: v for f, v in request.model_dump(exclude_unset=True).items() if v is not None or f == "condition"}
    listed = (await listed_quantities(session, [item.id])).get(item.id, 0)
    if listed:
        if changes.get("quantity", item.quantity) < listed:
            raise Conflict(f"{listed} of these are listed for sale; lower or remove those listings first")
        if any(changes.get(f, getattr(item, f)) != getattr(item, f) for f in ("finish", "language")):
            raise Conflict("Remove this card's listings before changing its finish or language")
    if "finish" in changes:
        check_finish(item.card, changes["finish"])
    for field, value in changes.items():
        setattr(item, field, value)
    await session.flush()
    return item


async def delete_collection_item(session: AsyncSession, current: CurrentUser, item_id: uuid.UUID) -> None:
    item = await get_own_collection_item(session, current, item_id, lock=True)
    await _release_finished_listings(session, [item.id])
    await session.delete(item)
    await session.flush()


# ----------------------------------------------------------------
# IMPORT
# ----------------------------------------------------------------
def _name_key(name: str) -> str:
    return name.strip().lower()


# Sets whose printings are odd defaults for a card named without a set.
_SPECIAL_SET_TYPES = {"memorabilia", "funny", "token", "minigame", "treasure_chest", "vanguard", "promo",
                      "box", "arsenal", "from_the_vault", "spellbook", "premium_deck"}


def pick_printing(candidates: list[Card], line: importer.ParsedLine) -> Card | None:
    """The best printing for a line, like Scryfall's default printing: in the
    requested finish if possible, English, already released, not a promo or
    special product, then newest."""
    if not candidates:
        return None
    wanted = line.finish or CardFinish.nonfoil
    today = date.today()
    return sorted(candidates, key=lambda c: (
        bool(c.finishes) and wanted.value not in c.finishes,
        c.lang != (line.language or "en") and c.lang != "en",
        c.lang != "en",
        c.released_at > today,
        c.promo,
        c.set_type in _SPECIAL_SET_TYPES,
        -c.released_at.toordinal(),
        len(c.collector_number), c.collector_number,
    ))[0]


def _same_card(card: Card, name: str | None) -> bool:
    if not name:
        return True
    wanted = _name_key(name)
    return wanted in (card.name.lower(), card.name.lower().split(" // ")[0])


async def _resolve(
    session: AsyncSession, lines: list[importer.ParsedLine]
) -> dict[int, tuple[Card, str | None]]:
    """Match parsed lines to printings. Returns {line number: (card, note)}."""
    numbered = {(ln.set_code, ln.collector_number.lower()) for ln in lines if ln.set_code and ln.collector_number}
    names = {_name_key(ln.name) for ln in lines if ln.name}

    by_number: dict[tuple[str, str], list[Card]] = {}
    if numbered:
        stmt = select(Card).where(func.lower(Card.collector_number).in_({n for _, n in numbered}),
                                  Card.set_code.in_({s for s, _ in numbered}))
        for printing in await session.scalars(stmt):
            by_number.setdefault((printing.set_code, printing.collector_number.lower()), []).append(printing)

    by_name: dict[str, list[Card]] = {}
    if names:
        # Decklists name double-faced cards by their front face.
        front = func.split_part(func.lower(Card.name), " // ", 1)
        stmt = select(Card).where(func.lower(Card.name).in_(names) | front.in_(names))
        for printing in await session.scalars(stmt):
            full_name = printing.name.lower()
            by_name.setdefault(full_name, []).append(printing)
            front_name = full_name.split(" // ")[0]
            if front_name != full_name:
                by_name.setdefault(front_name, []).append(printing)

    resolved: dict[int, tuple[Card, str | None]] = {}
    card: Card | None
    for ln in lines:
        name_matches = by_name.get(_name_key(ln.name), []) if ln.name else []
        if ln.set_code and ln.collector_number:
            card = pick_printing(by_number.get((ln.set_code, ln.collector_number.lower()), []), ln)
            # A number that points at a different card is a typo; fall back to the name.
            if card is not None and _same_card(card, ln.name):
                resolved[ln.line] = (card, None)
                continue
        if ln.set_code:
            card = pick_printing([c for c in name_matches if c.set_code == ln.set_code], ln)
            if card is not None:
                note = (None if not ln.collector_number
                        else f"#{ln.collector_number} isn't this card; matched by name in that set")
                resolved[ln.line] = (card, note)
                continue
        card = pick_printing(name_matches, ln)
        if card is not None:
            where = f"{card.set_name} ({card.set_code.upper()}) #{card.collector_number}"
            reason = "Set not found" if ln.set_code else "No set given"
            resolved[ln.line] = (card, f"{reason}; added the printing from {where}")
    return resolved


def _problem(error: importer.LineError) -> ImportProblem:
    return ImportProblem(line=error.line, text=error.text, error=error.error)


async def import_collection_items(
    session: AsyncSession, current: CurrentUser, collection_id: uuid.UUID, request: CollectionImport,
    dry_run: bool,
) -> ImportResult:
    """Add the cards in a decklist or CSV to a collection. Lines that can't be
    matched are reported, not fatal. Cards already in the collection with the
    same finish, condition and language get their quantity raised."""
    await get_own_collection(session, current, collection_id)
    parse = importer.parse_csv if request.format == "csv" else importer.parse_decklist
    try:
        lines, errors = parse(request.text)
    except importer.ImportTooLarge as e:
        raise BadRequest(str(e)) from e
    unmatched = [_problem(e) for e in errors]

    resolved = await _resolve(session, lines)
    matched: list[ImportedLine] = []
    for ln in lines:
        if ln.line not in resolved:
            unmatched.append(ImportProblem(line=ln.line, text=ln.text, error="No card with this name, set or number"))
            continue
        card, note = resolved[ln.line]
        finish = ln.finish or CardFinish.nonfoil
        if card.finishes and finish.value not in card.finishes:
            unmatched.append(ImportProblem(line=ln.line, text=ln.text,
                                           error=f"Not printed in {finish.value} ({', '.join(card.finishes)})"))
            continue
        matched.append(ImportedLine(
            line=ln.line, text=ln.text, card=CardRead.model_validate(card), quantity=ln.quantity,
            condition=ln.condition or request.condition, finish=finish,
            language=ln.language or request.language, note=note))
    unmatched.sort(key=lambda p: p.line)

    created = updated = 0
    if not dry_run and matched:
        created, updated = await _add_imported(session, collection_id, matched)
    return ImportResult(dry_run=dry_run, matched=matched, unmatched=unmatched,
                        items_created=created, items_updated=updated)


async def _add_imported(session: AsyncSession, collection_id: uuid.UUID, lines: list[ImportedLine]) -> tuple[int, int]:
    existing = {
        (i.card_id, i.finish, i.condition, i.language): i
        for i in await session.scalars(
            select(CollectionItem).with_for_update().where(
                CollectionItem.collection_id == collection_id,
                CollectionItem.card_id.in_({ln.card.id for ln in lines})))
    }
    created = updated = 0
    for ln in lines:
        k = (ln.card.id, ln.finish, ln.condition, ln.language)
        if k in existing:
            existing[k].quantity += ln.quantity
            updated += 1
            continue
        item = CollectionItem(collection_id=collection_id, card_id=ln.card.id, condition=ln.condition,
                              finish=ln.finish, language=ln.language, quantity=ln.quantity)
        session.add(item)
        existing[k] = item
        created += 1
    await session.flush()
    return created, updated
