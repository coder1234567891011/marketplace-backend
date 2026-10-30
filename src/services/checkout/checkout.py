"""
Checkout: turn the cart into one order per seller and one Stripe payment.

    start_checkout   holds the copies (quantity_available → quantity_reserved)
                     and opens a Stripe Checkout page that expires with the hold
    mark_paid        checkout.session.completed: the held copies are sold, the
                     sellers' collections shrink, orders become paid
    release          checkout.session.expired (or the buyer cancels): the held
                     copies go back on sale, orders are cancelled

Listings are always locked in id order, so concurrent checkouts touching the
same listings can't deadlock.
"""
import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth.auth import CurrentUser
from models.database import (
    CartItem,
    Checkout,
    CheckoutStatus,
    CollectionItem,
    Listing,
    ListingStatus,
    Order,
    OrderItem,
    OrderStatus,
    ShippingRate,
    User,
)
from models.object_models.orders import CheckoutCreate
from services.addresses.addresses import get_address
from services.cart.cart import listing_problem, load_cart
from services.checkout.fees import FeeTerms
from services.listings.listings import change_status
from services.orders.orders import TRANSITIONS, change_order_status, pay_out, with_items
from services.payments.stripe_gateway import CheckoutLine, get_gateway
from services.user.user import require_profile
from utils.errors import BadRequest, Conflict, NotFound
from utils.settings import settings

log = logging.getLogger(__name__)

CURRENCY = "usd"
# Stripe's smallest card payment in USD.
MIN_TOTAL_CENTS = 50
# Stripe rejects an expiry under 30 minutes away, measured when it receives
# the request, so the hold runs this much past the configured time.
EXPIRY_MARGIN = timedelta(minutes=1)


async def _lock_listings(session: AsyncSession, listing_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Listing]:
    stmt = (select(Listing).where(Listing.id.in_(set(listing_ids))).order_by(Listing.id)
            .with_for_update(of=Listing).execution_options(populate_existing=True)
            .options(selectinload(Listing.seller), selectinload(Listing.card)))
    return {listing.id: listing for listing in await session.scalars(stmt)}


async def _load_checkout(session: AsyncSession, checkout_id: uuid.UUID, lock: bool = False) -> Checkout | None:
    stmt = (select(Checkout).where(Checkout.id == checkout_id)
            .options(selectinload(Checkout.orders).options(with_items())))
    if lock:
        stmt = stmt.with_for_update(of=Checkout).execution_options(populate_existing=True)
    return await session.scalar(stmt)


async def get_checkout(session: AsyncSession, current: CurrentUser, checkout_id: uuid.UUID) -> Checkout:
    checkout = await _load_checkout(session, checkout_id)
    if checkout is None or checkout.buyer_id != current.id:
        raise NotFound("Checkout not found")
    return checkout


def _order_items(checkout: Checkout) -> list[OrderItem]:
    return [item for order in checkout.orders for item in order.items]


def _address_snapshot(address) -> dict[str, str | None]:  # type: ignore[no-untyped-def]
    return {field: getattr(address, field) for field in
            ("recipient_name", "line1", "line2", "city", "state", "postal_code", "country")}


async def _pick_rates(session: AsyncSession, seller_ids: Sequence[uuid.UUID], sellers: dict[uuid.UUID, User],
                      chosen: dict[uuid.UUID, uuid.UUID]) -> dict[uuid.UUID, ShippingRate]:
    stmt = (select(ShippingRate).where(ShippingRate.seller_id.in_(seller_ids))
            .order_by(ShippingRate.price_cents, ShippingRate.created_at))
    rates: dict[uuid.UUID, list[ShippingRate]] = defaultdict(list)
    for rate in await session.scalars(stmt):
        rates[rate.seller_id].append(rate)
    picked = {}
    for seller_id in seller_ids:
        options = rates[seller_id]
        if not options:
            raise Conflict(f"{sellers[seller_id].username} has no shipping options yet; "
                           "remove their cards from your cart to check out")
        if seller_id in chosen:
            match = next((r for r in options if r.id == chosen[seller_id]), None)
            if match is None:
                raise BadRequest(f"That shipping rate isn't one of {sellers[seller_id].username}'s")
            picked[seller_id] = match
        else:
            picked[seller_id] = options[0]
    return picked


async def _open_checkout(session: AsyncSession, buyer_id: uuid.UUID) -> Checkout | None:
    stmt = (select(Checkout).where(Checkout.buyer_id == buyer_id, Checkout.status == CheckoutStatus.open)
            .with_for_update())
    return await session.scalar(stmt)


async def _settle_stale(session: AsyncSession, checkout: Checkout) -> None:
    """A previous checkout is still open. Past its expiry Stripe has expired
    it too, so release it now instead of waiting for the webhook; otherwise
    the buyer must finish or cancel it first."""
    if checkout.expires_at > datetime.now(timezone.utc):
        raise Conflict(f"You have a checkout in progress ({checkout.id}); pay or cancel it first",
                       code="checkout_in_progress")
    status = await get_gateway().expire_checkout(checkout.stripe_checkout_session_id) \
        if checkout.stripe_checkout_session_id else "expired"
    if status != "expired":
        raise Conflict("Your previous checkout is being paid; try again in a minute", code="checkout_in_progress")
    await release(session, checkout.id)


async def start_checkout(session: AsyncSession, current: CurrentUser, request: CheckoutCreate) -> Checkout:
    buyer = await require_profile(session, current)
    previous = await _open_checkout(session, buyer.id)
    if previous is not None:
        await _settle_stale(session, previous)
    address = await get_address(session, current, request.shipping_address_id)
    if not address.recipient_name:
        raise BadRequest("Add the recipient's name to this address before checking out")

    cart = await load_cart(session, buyer.id, lock=True)
    if not cart:
        raise BadRequest("Your cart is empty")
    listings = await _lock_listings(session, (c.listing_id for c in cart))
    problems = []
    for line in cart:
        listing = listings[line.listing_id]
        problem = ("That's your own listing" if listing.seller_id == buyer.id
                   else listing_problem(listing, line.quantity))
        if listing.currency != CURRENCY:
            problem = f"Priced in {listing.currency.upper()}; only {CURRENCY.upper()} is supported"
        if problem:
            problems.append(f"{listing.card.name}: {problem}")
    if problems:
        raise Conflict("Some cart items can't be bought as they are. " + "; ".join(problems),
                       code="cart_changed")

    by_seller: dict[uuid.UUID, list[CartItem]] = defaultdict(list)
    for line in cart:
        by_seller[listings[line.listing_id].seller_id].append(line)
    sellers = {seller_id: listings[lines[0].listing_id].seller for seller_id, lines in by_seller.items()}
    rates = await _pick_rates(session, list(by_seller), sellers, request.shipping_rates)

    terms = FeeTerms.current()
    hold = timedelta(minutes=settings.checkout_hold_minutes) + EXPIRY_MARGIN
    checkout = Checkout(id=uuid.uuid4(), buyer_id=buyer.id, total_cents=0, currency=CURRENCY,
                        expires_at=datetime.now(timezone.utc) + hold)
    session.add(checkout)
    snapshot = _address_snapshot(address)
    lines: list[CheckoutLine] = []
    for seller_id, cart_lines in by_seller.items():
        rate = rates[seller_id]
        subtotal = sum(listings[c.listing_id].price_cents * c.quantity for c in cart_lines)
        fee = terms.fee_cents(subtotal, rate.price_cents)
        order = Order(
            checkout_id=checkout.id, buyer_id=buyer.id, seller_id=seller_id, currency=CURRENCY,
            subtotal_cents=subtotal, shipping_cents=rate.price_cents, total_cents=subtotal + rate.price_cents,
            fee_percent=terms.percent, fee_fixed_cents=terms.fixed_cents, platform_fee_cents=fee,
            seller_payout_cents=subtotal + rate.price_cents - fee, status=OrderStatus.pending,
            shipping_address_id=address.id, shipping_address=snapshot, shipping_rate_name=rate.name,
        )
        session.add(order)
        await session.flush()
        for c in cart_lines:
            listing = listings[c.listing_id]
            session.add(OrderItem(order_id=order.id, listing_id=listing.id, card_id=listing.card_id,
                                  condition=listing.condition, finish=listing.finish, language=listing.language,
                                  quantity=c.quantity, price_cents=listing.price_cents))
            listing.quantity_available -= c.quantity
            listing.quantity_reserved += c.quantity
            if listing.quantity_available == 0:
                change_status(listing, ListingStatus.pending_sale)
        checkout.total_cents += order.total_cents
        copies = sum(c.quantity for c in cart_lines)
        lines.append(CheckoutLine(
            name=f"Cards from {sellers[seller_id].display_name or sellers[seller_id].username}",
            description=f"{copies} card{'s' if copies != 1 else ''}, {rate.name} shipping",
            amount_cents=order.total_cents))
    if checkout.total_cents < MIN_TOTAL_CENTS:
        raise BadRequest(f"Card payments must be at least ${MIN_TOTAL_CENTS / 100:.2f}")
    await session.flush()

    # Last, so a refusal above never leaves an orphaned Stripe page. If the
    # commit fails after this, the page expires unused with nothing held.
    base = settings.frontend_url.rstrip("/")
    hosted = await get_gateway().create_checkout(
        str(checkout.id), lines, CURRENCY, checkout.expires_at,
        success_url=f"{base}/checkout/{checkout.id}?result=success",
        cancel_url=f"{base}/checkout/{checkout.id}?result=cancelled",
        customer_email=current.email)
    checkout.stripe_checkout_session_id = hosted.session_id
    checkout.stripe_checkout_url = hosted.url
    await session.flush()
    loaded = await _load_checkout(session, checkout.id)
    assert loaded is not None
    return loaded


async def cancel_checkout(session: AsyncSession, current: CurrentUser, checkout_id: uuid.UUID) -> Checkout:
    """The buyer gives up before paying: the copies go back on sale now."""
    checkout = await get_checkout(session, current, checkout_id)
    if checkout.status == CheckoutStatus.expired:
        return checkout
    if checkout.status == CheckoutStatus.paid:
        raise Conflict("This checkout is already paid")
    status = await get_gateway().expire_checkout(checkout.stripe_checkout_session_id) \
        if checkout.stripe_checkout_session_id else "expired"
    if status != "expired":
        raise Conflict("Payment is already going through for this checkout")
    await release(session, checkout.id)
    reloaded = await _load_checkout(session, checkout.id)
    assert reloaded is not None
    return reloaded


async def release(session: AsyncSession, checkout_id: uuid.UUID) -> None:
    """Return an unpaid checkout's copies to sale and cancel its orders."""
    checkout = await _load_checkout(session, checkout_id, lock=True)
    if checkout is None or checkout.status != CheckoutStatus.open:
        return
    checkout.status = CheckoutStatus.expired
    checkout.stripe_checkout_url = None
    items = _order_items(checkout)
    listings = await _lock_listings(session, (i.listing_id for i in items))
    for item in items:
        listing = listings[item.listing_id]
        listing.quantity_reserved -= item.quantity
        listing.quantity_available += item.quantity
        if listing.status == ListingStatus.pending_sale:
            change_status(listing, ListingStatus.active)
    for order in checkout.orders:
        change_order_status(order, OrderStatus.cancelled)
    await session.flush()


async def _sell_from_collection(session: AsyncSession, listing: Listing, quantity: int) -> None:
    """Sold copies leave the seller's collection; an item with none left is deleted."""
    if listing.collection_item_id is None:
        return
    item = await session.scalar(select(CollectionItem).where(CollectionItem.id == listing.collection_item_id)
                                .with_for_update())
    if item is None:
        return
    item.quantity -= quantity
    if item.quantity <= 0:
        # Sold listings keep their history but stop pointing at the item.
        await session.execute(update(Listing).where(Listing.collection_item_id == item.id)
                              .values(collection_item_id=None))
        await session.delete(item)


async def mark_paid(session: AsyncSession, checkout_id: uuid.UUID, payment_intent_id: str) -> None:
    """Stripe took the payment. Safe to run twice."""
    checkout = await _load_checkout(session, checkout_id, lock=True)
    if checkout is None:
        log.warning("Payment for unknown checkout %s", checkout_id)
        return
    if checkout.status == CheckoutStatus.paid:
        return
    gateway = get_gateway()
    if checkout.status == CheckoutStatus.expired:
        # Shouldn't happen (Stripe can't complete an expired page), but if it
        # does the copies may be gone: give the money back.
        log.error("Checkout %s was paid after it expired; refunding", checkout_id)
        await gateway.refund(f"checkout-{checkout.id}", payment_intent_id, checkout.total_cents)
        return

    now = datetime.now(timezone.utc)
    checkout.status = CheckoutStatus.paid
    checkout.stripe_payment_intent_id = payment_intent_id
    checkout.stripe_charge_id = await gateway.charge_id(payment_intent_id)
    checkout.stripe_checkout_url = None
    checkout.paid_at = now

    items = _order_items(checkout)
    listings = await _lock_listings(session, (i.listing_id for i in items))
    for item in items:
        listing = listings[item.listing_id]
        listing.quantity_reserved -= item.quantity
        if listing.quantity_available == 0 and listing.quantity_reserved == 0:
            change_status(listing, ListingStatus.sold)
        await _sell_from_collection(session, listing, item.quantity)
    for order in checkout.orders:
        change_order_status(order, OrderStatus.paid)
        order.paid_at = now
        await session.execute(update(User).where(User.id == order.seller_id)
                              .values(total_sales=User.total_sales + 1))
    await session.execute(delete(CartItem).where(CartItem.user_id == checkout.buyer_id,
                                                 CartItem.listing_id.in_(list(listings))))
    await session.flush()
    if settings.payout_on == "paid":
        for order in checkout.orders:
            await pay_out(session, order)
        await session.flush()


async def mark_disputed(session: AsyncSession, payment_intent_id: str) -> None:
    """The buyer disputed the charge with their bank. One charge covers the
    whole checkout, so every order in it is flagged."""
    checkout = await session.scalar(select(Checkout).where(Checkout.stripe_payment_intent_id == payment_intent_id)
                                    .options(selectinload(Checkout.orders)))
    if checkout is None:
        return
    for order in checkout.orders:
        if OrderStatus.disputed in TRANSITIONS[order.status]:
            change_order_status(order, OrderStatus.disputed)
    await session.flush()
