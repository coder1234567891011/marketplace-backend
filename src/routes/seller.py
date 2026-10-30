from fastapi import APIRouter

from auth.auth import AuthUser
from models.object_models.sellers import SellerStatus, StripeLink
from services.persistence.database import SessionDep
from services.sellers.sellers import dashboard_link, get_seller_status, start_onboarding
from utils import limits

router = APIRouter(
    prefix="/sellers",
    tags=["sellers"],
)


@router.get("/me")
async def read_seller_status(current: AuthUser, session: SessionDep) -> SellerStatus:
    """Whether the caller has set up payouts and their listings can be bought."""
    return await get_seller_status(session, current)


@router.post("/me/onboarding", dependencies=[limits.SELLER_LINKS])
async def onboarding_link(current: AuthUser, session: SessionDep) -> StripeLink:
    """Stripe's payout setup forms. Redirect to the url straight away: it expires within minutes."""
    return StripeLink(url=await start_onboarding(session, current))


@router.post("/me/dashboard", dependencies=[limits.SELLER_LINKS])
async def stripe_dashboard_link(current: AuthUser, session: SessionDep) -> StripeLink:
    """The seller's Stripe dashboard, for payouts and bank details."""
    return StripeLink(url=await dashboard_link(session, current))
