import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth.auth import CurrentUser
from models.database import ShippingRate
from models.object_models.shipping import ShippingRateCreate, ShippingRateUpdate
from services.user.user import require_profile
from utils.errors import Conflict, NotFound

MAX_RATES = 10


async def get_shipping_rates(session: AsyncSession, seller_id: uuid.UUID) -> Sequence[ShippingRate]:
    """A seller's shipping options, cheapest first."""
    stmt = (select(ShippingRate).where(ShippingRate.seller_id == seller_id)
            .order_by(ShippingRate.price_cents, ShippingRate.created_at))
    return (await session.scalars(stmt)).all()


async def _get_own_rate(session: AsyncSession, current: CurrentUser, rate_id: uuid.UUID) -> ShippingRate:
    rate = await session.get(ShippingRate, rate_id)
    if rate is None or rate.seller_id != current.id:
        raise NotFound("Shipping rate not found")
    return rate


async def create_shipping_rate(
    session: AsyncSession, current: CurrentUser, request: ShippingRateCreate
) -> ShippingRate:
    await require_profile(session, current)
    count = await session.scalar(select(func.count()).where(ShippingRate.seller_id == current.id))
    if count is not None and count >= MAX_RATES:
        raise Conflict(f"You can have up to {MAX_RATES} shipping rates")
    rate = ShippingRate(seller_id=current.id, **request.model_dump())
    session.add(rate)
    await session.flush()
    return rate


async def update_shipping_rate(
    session: AsyncSession, current: CurrentUser, rate_id: uuid.UUID, request: ShippingRateUpdate
) -> ShippingRate:
    rate = await _get_own_rate(session, current, rate_id)
    for field, value in request.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(rate, field, value)
    await session.flush()
    return rate


async def delete_shipping_rate(session: AsyncSession, current: CurrentUser, rate_id: uuid.UUID) -> None:
    rate = await _get_own_rate(session, current, rate_id)
    await session.delete(rate)
    await session.flush()
