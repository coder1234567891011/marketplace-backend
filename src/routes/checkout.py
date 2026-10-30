import uuid

from fastapi import APIRouter, status

from auth.auth import AuthUser
from models.object_models.orders import CheckoutCreate, CheckoutRead
from services.checkout.checkout import cancel_checkout, get_checkout, start_checkout
from services.persistence.database import SessionDep
from utils import limits

router = APIRouter(
    prefix="/checkout",
    tags=["checkout"],
)


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[limits.CHECKOUT])
async def create_checkout(request: CheckoutCreate, current: AuthUser, session: SessionDep) -> CheckoutRead:
    """Hold the cart's copies and open a Stripe payment page (url) for them,
    one order per seller. The hold ends at expires_at if unpaid."""
    return CheckoutRead.model_validate(await start_checkout(session, current, request))


@router.get("/{checkout_id}")
async def read_checkout(checkout_id: uuid.UUID, current: AuthUser, session: SessionDep) -> CheckoutRead:
    """Poll this after Stripe sends the buyer back: status becomes paid once Stripe confirms."""
    return CheckoutRead.model_validate(await get_checkout(session, current, checkout_id))


@router.post("/{checkout_id}/cancel", dependencies=[limits.CHECKOUT])
async def abandon_checkout(checkout_id: uuid.UUID, current: AuthUser, session: SessionDep) -> CheckoutRead:
    """Give up before paying; the copies go back on sale straight away."""
    return CheckoutRead.model_validate(await cancel_checkout(session, current, checkout_id))
