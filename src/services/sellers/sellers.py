"""
Seller onboarding with Stripe Connect (Express accounts). Stripe hosts the
onboarding forms (identity, bank account); a seller's listings can be bought
once their account can receive transfers (users.stripe_connect_onboarded).
"""
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from auth.auth import CurrentUser
from models.database import User
from models.object_models.sellers import SellerStatus
from services.payments.stripe_gateway import get_gateway
from services.user.user import require_profile
from utils.errors import Conflict
from utils.settings import settings


async def get_seller_status(session: AsyncSession, current: CurrentUser) -> SellerStatus:
    """Also refreshes the flag from Stripe, in case a webhook was missed."""
    user = await require_profile(session, current)
    if user.stripe_connect_account_id and not user.stripe_connect_onboarded:
        user.stripe_connect_onboarded = await get_gateway().account_ready(user.stripe_connect_account_id)
        await session.flush()
    return SellerStatus(stripe_account_created=user.stripe_connect_account_id is not None,
                        can_sell=user.stripe_connect_onboarded)


async def start_onboarding(session: AsyncSession, current: CurrentUser) -> str:
    """A link to Stripe's onboarding forms, creating the seller's account on
    first use. Links expire after a few minutes, so fetch one right before
    redirecting."""
    user = await require_profile(session, current)
    gateway = get_gateway()
    if user.stripe_connect_account_id is None:
        user.stripe_connect_account_id = await gateway.create_account(str(user.id), current.email)
        await session.flush()
    page = f"{settings.frontend_url.rstrip('/')}/sell/onboarding"
    return await gateway.onboarding_link(user.stripe_connect_account_id, refresh_url=f"{page}?refresh=1",
                                         return_url=f"{page}?done=1")


async def dashboard_link(session: AsyncSession, current: CurrentUser) -> str:
    """A one-time login link to the seller's Stripe Express dashboard (payouts, bank details)."""
    user = await require_profile(session, current)
    if user.stripe_connect_account_id is None or not user.stripe_connect_onboarded:
        raise Conflict("Finish setting up payouts first")
    return await get_gateway().dashboard_link(user.stripe_connect_account_id)


async def account_updated(session: AsyncSession, account_id: str, ready: bool) -> None:
    """From the account.updated webhook: Stripe enabled or restricted an account."""
    await session.execute(update(User).where(User.stripe_connect_account_id == account_id)
                          .values(stripe_connect_onboarded=ready))
