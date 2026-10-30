from typing import Annotated

import stripe
from fastapi import APIRouter, Header, Request

from services.payments.stripe_gateway import get_gateway
from services.payments.webhooks import handle_event
from services.persistence.database import SessionDep
from utils.errors import BadRequest

router = APIRouter(
    prefix="/stripe",
    tags=["stripe"],
)


@router.post("/webhook", include_in_schema=False)
async def stripe_webhook(
    request: Request, session: SessionDep, stripe_signature: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    """Called by Stripe, not clients. Authenticated by the signature header."""
    gateway = get_gateway()
    if stripe_signature is None:
        raise BadRequest("Missing Stripe-Signature header")
    try:
        event = gateway.parse_webhook(await request.body(), stripe_signature)
    except (ValueError, stripe.SignatureVerificationError) as e:
        raise BadRequest("Invalid Stripe webhook") from e
    await handle_event(session, event)
    return {"received": True}
