"""
The buyer's cart: listings and how many copies of each. Adding to the cart
holds nothing; copies are only held once the buyer starts checkout.
"""
import uuid
from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth.auth import CurrentUser
from models.database import AccountStatus, CartItem, Listing, ListingStatus, ShippingRate
from models.object_models.cart import CartItemRead, CartRead, CartSeller
from models.object_models.listings import ListingRead
from models.object_models.shipping import ShippingRateRead
from models.object_models.user import UserRead
from services.listings.listings import get_listing
from services.user.user import require_profile
from utils.errors import BadRequest, Conflict

MAX_CART_LINES = 200


def listing_problem(listing: Listing, quantity: int) -> str | None:
    """Why quantity copies of listing can't be bought right now. Needs listing.seller loaded."""
    seller = listing.seller
    if (listing.status not in (ListingStatus.active, ListingStatus.pending_sale)
            or seller.account_status != AccountStatus.active or not seller.stripe_connect_onboarded):
        return "No longer for sale"
    if quantity > listing.quantity_available:
        if listing.quantity_available == 0:
            return "All copies are in other buyers' checkouts"
        return f"Only {listing.quantity_available} available"
    return None


async def load_cart(session: AsyncSession, user_id: uuid.UUID, lock: bool = False) -> Sequence[CartItem]:
    stmt = (select(CartItem).where(CartItem.user_id == user_id).order_by(CartItem.created_at, CartItem.id)
            .options(selectinload(CartItem.listing).options(selectinload(Listing.seller),
                                                            selectinload(Listing.card))))
    if lock:
        stmt = stmt.with_for_update(of=CartItem)
    return (await session.scalars(stmt)).all()


async def get_cart(session: AsyncSession, current: CurrentUser) -> CartRead:
    items = await load_cart(session, current.id)
    by_seller: dict[uuid.UUID, list[CartItem]] = defaultdict(list)
    for item in items:
        by_seller[item.listing.seller_id].append(item)
    rates: dict[uuid.UUID, list[ShippingRate]] = defaultdict(list)
    if by_seller:
        stmt = (select(ShippingRate).where(ShippingRate.seller_id.in_(by_seller))
                .order_by(ShippingRate.price_cents, ShippingRate.created_at))
        for rate in await session.scalars(stmt):
            rates[rate.seller_id].append(rate)

    sellers = []
    can_check_out = bool(items)
    for seller_id, seller_items in by_seller.items():
        lines = [CartItemRead(listing=ListingRead.model_validate(i.listing), quantity=i.quantity,
                              problem=listing_problem(i.listing, i.quantity)) for i in seller_items]
        can_check_out &= all(line.problem is None for line in lines) and bool(rates[seller_id])
        sellers.append(CartSeller(
            seller=UserRead.model_validate(seller_items[0].listing.seller), items=lines,
            subtotal_cents=sum(i.listing.price_cents * i.quantity for i in seller_items),
            shipping_rates=[ShippingRateRead.model_validate(r) for r in rates[seller_id]],
        ))
    return CartRead(sellers=sellers, subtotal_cents=sum(s.subtotal_cents for s in sellers),
                    item_count=sum(i.quantity for i in items), can_check_out=can_check_out)


async def set_cart_item(session: AsyncSession, current: CurrentUser, listing_id: uuid.UUID, quantity: int) -> None:
    """Add a listing to the cart, or change how many copies of it."""
    await require_profile(session, current)
    listing = await get_listing(session, listing_id)
    if listing.seller_id == current.id:
        raise BadRequest("You can't buy your own listing")
    problem = listing_problem(listing, quantity)
    if problem is not None:
        raise Conflict(problem)
    lines = await session.scalar(select(func.count()).where(CartItem.user_id == current.id,
                                                            CartItem.listing_id != listing_id))
    if lines is not None and lines >= MAX_CART_LINES:
        raise Conflict(f"Your cart can hold up to {MAX_CART_LINES} listings")
    stmt = insert(CartItem).values(id=uuid.uuid4(), user_id=current.id, listing_id=listing_id, quantity=quantity)
    await session.execute(stmt.on_conflict_do_update(index_elements=["user_id", "listing_id"],
                                                     set_={"quantity": quantity}))


async def remove_cart_item(session: AsyncSession, current: CurrentUser, listing_id: uuid.UUID) -> None:
    await session.execute(delete(CartItem).where(CartItem.user_id == current.id, CartItem.listing_id == listing_id))


async def clear_cart(session: AsyncSession, current: CurrentUser) -> None:
    await session.execute(delete(CartItem).where(CartItem.user_id == current.id))
