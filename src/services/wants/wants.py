"""
Wants lists: cards a user is looking for, what they already own, what's for
sale, price alerts, and the cheapest way to buy the list (optimizer.py).
"""
import uuid
from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth.auth import CurrentUser
from models.database import (
    Card,
    CardCondition,
    Collection,
    CollectionItem,
    Listing,
    ShippingRate,
    User,
    WantItem,
    WantsList,
)
from models.object_models.cards import CardRead
from models.object_models.collections import ImportProblem
from models.object_models.listings import ListingRead
from models.object_models.user import UserRead
from models.object_models.wants import (
    MissingItem,
    OptimizeRequest,
    OptimizeResult,
    PlannedLine,
    PlannedSeller,
    WantItemCreate,
    WantItemRead,
    WantItemStatus,
    WantItemUpdate,
    WantsImport,
    WantsImportResult,
    WantsListCreate,
)
from services.cards.cards import load_cards
from services.cart.cart import set_cart_item
from services.collections import importer
from services.collections.collections import check_finish, resolve_lines
from services.listings.listings import for_sale_conditions
from services.user.user import require_profile
from services.wants.optimizer import Offer, optimize
from utils.errors import BadRequest, Conflict, NotFound

MAX_LISTS = 50
MAX_ITEMS = 500
# Best to worst.
CONDITIONS = list(CardCondition)


# ----------------------------------------------------------------
# LISTS
# ----------------------------------------------------------------
async def get_wants_lists(session: AsyncSession, current: CurrentUser) -> list[tuple[WantsList, int]]:
    """The caller's lists, newest first, with how many items each has."""
    stmt = (select(WantsList, func.count(WantItem.id))
            .outerjoin(WantItem, WantItem.wants_list_id == WantsList.id)
            .where(WantsList.user_id == current.id).group_by(WantsList.id)
            .order_by(WantsList.created_at.desc(), WantsList.id))
    return [(wl, n) for wl, n in await session.execute(stmt)]


async def get_own_list(session: AsyncSession, current: CurrentUser, list_id: uuid.UUID,
                       lock: bool = False) -> WantsList:
    stmt = select(WantsList).where(WantsList.id == list_id, WantsList.user_id == current.id)
    if lock:
        stmt = stmt.with_for_update()
    wants_list = await session.scalar(stmt)
    if wants_list is None:
        raise NotFound("Wants list not found")
    return wants_list


async def create_list(session: AsyncSession, current: CurrentUser, request: WantsListCreate) -> WantsList:
    await require_profile(session, current)
    count = await session.scalar(select(func.count()).where(WantsList.user_id == current.id))
    if count is not None and count >= MAX_LISTS:
        raise Conflict(f"You can have up to {MAX_LISTS} wants lists")
    wants_list = WantsList(user_id=current.id, name=request.name)
    session.add(wants_list)
    await session.flush()
    return wants_list


async def rename_list(session: AsyncSession, current: CurrentUser, list_id: uuid.UUID,
                      request: WantsListCreate) -> WantsList:
    wants_list = await get_own_list(session, current, list_id, lock=True)
    wants_list.name = request.name
    await session.flush()
    return wants_list


async def delete_list(session: AsyncSession, current: CurrentUser, list_id: uuid.UUID) -> None:
    await session.delete(await get_own_list(session, current, list_id, lock=True))
    await session.flush()


# ----------------------------------------------------------------
# ITEMS
# ----------------------------------------------------------------
async def load_items(session: AsyncSession, list_id: uuid.UUID) -> Sequence[WantItem]:
    stmt = (select(WantItem).where(WantItem.wants_list_id == list_id)
            .options(selectinload(WantItem.card)).order_by(WantItem.created_at, WantItem.id))
    return (await session.scalars(stmt)).all()


async def _check_room(session: AsyncSession, list_id: uuid.UUID, adding: int) -> None:
    count = await session.scalar(select(func.count()).where(WantItem.wants_list_id == list_id)) or 0
    if count + adding > MAX_ITEMS:
        raise Conflict(f"A wants list can hold up to {MAX_ITEMS} cards")


async def add_item(session: AsyncSession, current: CurrentUser, list_id: uuid.UUID,
                   request: WantItemCreate) -> WantItem:
    await get_own_list(session, current, list_id, lock=True)
    await _check_room(session, list_id, 1)
    card = (await load_cards(session, [request.card_id]))[request.card_id]
    if request.finish:
        check_finish(card, request.finish)
    item = WantItem(wants_list_id=list_id, oracle_id=card.oracle_id, **request.model_dump())
    session.add(item)
    await session.flush()
    return await _reload(session, item.id)


async def _reload(session: AsyncSession, item_id: uuid.UUID) -> WantItem:
    stmt = select(WantItem).where(WantItem.id == item_id).options(selectinload(WantItem.card))
    return (await session.scalars(stmt.execution_options(populate_existing=True))).one()


async def _own_item(session: AsyncSession, current: CurrentUser, item_id: uuid.UUID) -> WantItem:
    stmt = (select(WantItem).join(WantsList).where(WantItem.id == item_id, WantsList.user_id == current.id)
            .options(selectinload(WantItem.card)).with_for_update(of=WantItem))
    item = await session.scalar(stmt)
    if item is None:
        raise NotFound("Wants list item not found")
    return item


async def update_item(session: AsyncSession, current: CurrentUser, item_id: uuid.UUID,
                      request: WantItemUpdate) -> WantItem:
    item = await _own_item(session, current, item_id)
    changes = request.model_dump(exclude_unset=True)
    for required in ("any_printing", "quantity"):
        if changes.get(required, 0) is None:
            del changes[required]
    if changes.get("finish"):
        check_finish(item.card, changes["finish"])
    for name, value in changes.items():
        setattr(item, name, value)
    await session.flush()
    return await _reload(session, item.id)


async def delete_item(session: AsyncSession, current: CurrentUser, item_id: uuid.UUID) -> None:
    await session.delete(await _own_item(session, current, item_id))
    await session.flush()


async def import_items(session: AsyncSession, current: CurrentUser, list_id: uuid.UUID,
                       request: WantsImport) -> WantsImportResult:
    """Add a decklist's cards. A card already on the list (same printing rule,
    finish and language) gets its quantity raised."""
    await get_own_list(session, current, list_id, lock=True)
    try:
        lines, errors = importer.parse_decklist(request.text)
    except importer.ImportTooLarge as e:
        raise BadRequest(str(e)) from e
    unmatched = [ImportProblem(line=e.line, text=e.text, error=e.error) for e in errors]
    resolved = await resolve_lines(session, lines)

    existing = {(i.oracle_id if i.any_printing else i.card_id, i.any_printing, i.finish, i.language): i
                for i in await load_items(session, list_id)}
    added = updated = 0
    new_items: list[WantItem] = []
    for ln in lines:
        if ln.line not in resolved:
            unmatched.append(ImportProblem(line=ln.line, text=ln.text, error="No card with this name, set or number"))
            continue
        card, note = resolved[ln.line]
        # A line naming its set and number wants that printing, unless it had to be matched by name.
        exact = bool(ln.set_code and ln.collector_number and note is None)
        key = (card.id if exact else card.oracle_id, not exact, ln.finish, ln.language)
        if key in existing:
            existing[key].quantity += ln.quantity
            updated += 1
            continue
        item = WantItem(wants_list_id=list_id, card_id=card.id, oracle_id=card.oracle_id, any_printing=not exact,
                        quantity=ln.quantity, min_condition=ln.condition or request.min_condition,
                        finish=ln.finish, language=ln.language)
        existing[key] = item
        new_items.append(item)
        added += 1
    await _check_room(session, list_id, len(new_items))
    session.add_all(new_items)
    await session.flush()
    unmatched.sort(key=lambda p: p.line)
    return WantsImportResult(added=added, updated=updated, unmatched=unmatched)


# ----------------------------------------------------------------
# WHAT'S OWNED AND FOR SALE
# ----------------------------------------------------------------
def condition_ok(item: WantItem, condition: CardCondition) -> bool:
    return item.min_condition is None or CONDITIONS.index(condition) <= CONDITIONS.index(item.min_condition)


def _same_card(item: WantItem, card: Card, finish: str, language: str) -> bool:
    return ((card.oracle_id == item.oracle_id if item.any_printing else card.id == item.card_id)
            and (item.finish is None or item.finish == finish)
            and (item.language is None or item.language == language))


def listing_matches(item: WantItem, listing: Listing) -> bool:
    return _same_card(item, listing.card, listing.finish, listing.language) and condition_ok(item, listing.condition)


async def listings_for(session: AsyncSession, items: Sequence[WantItem],
                       buyer_id: uuid.UUID) -> dict[uuid.UUID, list[Listing]]:
    """For-sale listings matching each item (not the buyer's own), cheapest first."""
    if not items:
        return {}
    stmt = (select(Listing).join(User, User.id == Listing.seller_id).join(Card, Card.id == Listing.card_id)
            .where(*for_sale_conditions(), Card.oracle_id.in_({i.oracle_id for i in items}),
                   Listing.seller_id != buyer_id)
            .options(selectinload(Listing.seller), selectinload(Listing.card))
            .order_by(Listing.price_cents, Listing.id))
    listings = (await session.scalars(stmt)).all()
    return {item.id: [lst for lst in listings if listing_matches(item, lst)] for item in items}


async def owned_for(session: AsyncSession, items: Sequence[WantItem], user_id: uuid.UUID) -> dict[uuid.UUID, int]:
    """Copies of each item in the user's collections, in any condition."""
    if not items:
        return {}
    stmt = (select(CollectionItem).join(Collection).join(Card, Card.id == CollectionItem.card_id)
            .where(Collection.user_id == user_id, Card.oracle_id.in_({i.oracle_id for i in items}))
            .options(selectinload(CollectionItem.card)))
    owned = (await session.scalars(stmt)).all()
    return {item.id: sum(c.quantity for c in owned if _same_card(item, c.card, c.finish, c.language))
            for item in items}


async def list_status(session: AsyncSession, current: CurrentUser, list_id: uuid.UUID) -> list[WantItemStatus]:
    await get_own_list(session, current, list_id)
    items = await load_items(session, list_id)
    listings = await listings_for(session, items, current.id)
    owned = await owned_for(session, items, current.id)
    return [WantItemStatus(
        **WantItemRead.model_validate(i).model_dump(), owned=owned[i.id],
        available=sum(lst.quantity_available for lst in listings[i.id]),
        lowest_price_cents=listings[i.id][0].price_cents if listings[i.id] else None,
    ) for i in items]


async def price_alerts(session: AsyncSession, current: CurrentUser, list_id: uuid.UUID) -> list[ListingRead]:
    """Listings at or under an item's target price, cheapest first."""
    await get_own_list(session, current, list_id)
    items = [i for i in await load_items(session, list_id) if i.max_price_cents is not None]
    listings = await listings_for(session, items, current.id)
    hits = {lst.id: lst for i in items for lst in listings[i.id] if lst.price_cents <= (i.max_price_cents or 0)}
    return [ListingRead.model_validate(lst) for lst in sorted(hits.values(), key=lambda x: (x.price_cents, x.id))]


# ----------------------------------------------------------------
# OPTIMIZER
# ----------------------------------------------------------------
async def optimize_list(session: AsyncSession, current: CurrentUser, list_id: uuid.UUID,
                        request: OptimizeRequest) -> OptimizeResult:
    """The cheapest way found to buy the list, optionally put in the cart."""
    await get_own_list(session, current, list_id)
    items = await load_items(session, list_id)
    owned = await owned_for(session, items, current.id) if request.skip_owned else {}
    needs = {i.id: i.quantity - owned.get(i.id, 0) for i in items if i.quantity > owned.get(i.id, 0)}
    wanted = [i for i in items if i.id in needs]
    listings = await listings_for(session, wanted, current.id)
    by_id = {lst.id: lst for found in listings.values() for lst in found}

    # Each seller's cheapest rate, which checkout picks by default; sellers without rates can't be bought from.
    rates: dict[uuid.UUID, ShippingRate] = {}
    if by_id:
        stmt = (select(ShippingRate).where(ShippingRate.seller_id.in_({lst.seller_id for lst in by_id.values()}))
                .order_by(ShippingRate.price_cents, ShippingRate.created_at))
        for rate in await session.scalars(stmt):
            rates.setdefault(rate.seller_id, rate)

    plan = optimize(
        needs,
        {item: [Offer(lst.id, lst.seller_id, lst.price_cents) for lst in found] for item, found in listings.items()},
        {lst.id: lst.quantity_available for lst in by_id.values()},
        {seller: rate.price_cents for seller, rate in rates.items()},
    )

    lines_by_seller: dict[uuid.UUID, list[PlannedLine]] = defaultdict(list)
    for item_id, picks in plan.picks.items():
        for listing_id, copies in picks.items():
            listing = by_id[listing_id]
            lines_by_seller[listing.seller_id].append(PlannedLine(
                want_item_id=item_id, listing=ListingRead.model_validate(listing), quantity=copies))
    sellers = [PlannedSeller(
        seller=UserRead.model_validate(lines[0].listing.seller), lines=lines,
        subtotal_cents=sum(ln.listing.price_cents * ln.quantity for ln in lines),
        shipping_rate_id=rates[seller_id].id, shipping_cents=rates[seller_id].price_cents,
    ) for seller_id, lines in lines_by_seller.items()]
    sellers.sort(key=lambda s: -s.subtotal_cents)
    item_by_id = {i.id: i for i in items}
    missing = [MissingItem(want_item_id=item_id, card=CardRead.model_validate(item_by_id[item_id].card), quantity=n)
               for item_id, n in plan.missing.items()]

    if request.add_to_cart:
        per_listing: dict[uuid.UUID, int] = defaultdict(int)
        for s in sellers:
            for ln in s.lines:
                per_listing[ln.listing.id] += ln.quantity
        for listing_id, copies in per_listing.items():
            await set_cart_item(session, current, listing_id, copies)

    subtotal = sum(s.subtotal_cents for s in sellers)
    shipping = sum(s.shipping_cents for s in sellers)
    return OptimizeResult(sellers=sellers, subtotal_cents=subtotal, shipping_cents=shipping,
                          total_cents=subtotal + shipping, missing=missing, added_to_cart=request.add_to_cart)
