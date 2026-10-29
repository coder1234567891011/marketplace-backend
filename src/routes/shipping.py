import uuid

from fastapi import APIRouter, status

from auth.auth import AuthUser
from models.object_models.shipping import ShippingRateCreate, ShippingRateRead, ShippingRateUpdate
from services.persistence.database import SessionDep
from services.shipping.shipping import (
    create_shipping_rate,
    delete_shipping_rate,
    get_shipping_rates,
    update_shipping_rate,
)
from utils import limits

router = APIRouter(
    prefix="/shipping-rates",
    tags=["shipping"],
)


@router.get("/user/{user_id}")
async def list_shipping_rates(user_id: uuid.UUID, session: SessionDep) -> list[ShippingRateRead]:
    """A seller's flat shipping options, cheapest first."""
    return [ShippingRateRead.model_validate(r) for r in await get_shipping_rates(session, user_id)]


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[limits.SHIPPING])
async def add_shipping_rate(request: ShippingRateCreate, current: AuthUser, session: SessionDep) -> ShippingRateRead:
    return ShippingRateRead.model_validate(await create_shipping_rate(session, current, request))


@router.patch("/{rate_id}", dependencies=[limits.SHIPPING])
async def edit_shipping_rate(
    rate_id: uuid.UUID, request: ShippingRateUpdate, current: AuthUser, session: SessionDep
) -> ShippingRateRead:
    return ShippingRateRead.model_validate(await update_shipping_rate(session, current, rate_id, request))


@router.delete("/{rate_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[limits.SHIPPING])
async def remove_shipping_rate(rate_id: uuid.UUID, current: AuthUser, session: SessionDep) -> None:
    await delete_shipping_rate(session, current, rate_id)
