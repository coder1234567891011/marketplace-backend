# ----------------------------------------------------------------
# SELLERS (Stripe Connect onboarding)
# ----------------------------------------------------------------
from pydantic import BaseModel


class SellerStatus(BaseModel):
    stripe_account_created: bool
    # Listings can be bought once Stripe can pay this seller.
    can_sell: bool


class StripeLink(BaseModel):
    """A short-lived Stripe page to redirect to."""
    url: str
