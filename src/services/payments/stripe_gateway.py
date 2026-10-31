"""
Every call the API makes to Stripe, in one place. Services call
get_gateway() rather than the Stripe SDK, so tests can swap in a fake
(tests/fake_stripe.py) and the rest of the code never sees Stripe objects.

Money moves with "separate charges and transfers": the buyer pays the
platform once per checkout, then each seller's share is transferred to their
Connect (Express) account, funded from that charge.
"""
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any, Protocol

import stripe

from utils.errors import AppError
from utils.settings import settings


class PaymentsUnavailable(AppError):
    status_code = 503
    code = "payments_unavailable"


class StripeFailed(AppError):
    """Stripe refused a request (e.g. a seller's account can't receive transfers yet)."""
    status_code = 502
    code = "payment_provider_error"


@dataclass
class CheckoutLine:
    name: str
    description: str
    amount_cents: int


@dataclass
class HostedCheckout:
    session_id: str
    url: str


class Gateway(Protocol):
    async def create_account(self, user_id: str, email: str | None) -> str: ...
    async def onboarding_link(self, account_id: str, refresh_url: str, return_url: str) -> str: ...
    async def dashboard_link(self, account_id: str) -> str: ...
    async def account_ready(self, account_id: str) -> bool: ...
    async def create_checkout(
        self, checkout_id: str, lines: list[CheckoutLine], currency: str, expires_at: datetime,
        success_url: str, cancel_url: str, customer_email: str | None,
    ) -> HostedCheckout: ...
    async def expire_checkout(self, session_id: str) -> str: ...
    async def charge_id(self, payment_intent_id: str) -> str: ...
    async def transfer(self, order_id: str, amount_cents: int, currency: str, account_id: str,
                       charge_id: str, transfer_group: str) -> str: ...
    # key identifies the refund (an order id, "issue-<id>", "checkout-<id>") and
    # makes retries idempotent.
    async def reverse_transfer(self, key: str, transfer_id: str, amount_cents: int) -> None: ...
    async def refund(self, key: str, payment_intent_id: str, amount_cents: int) -> str: ...
    def parse_webhook(self, payload: bytes, signature: str) -> dict[str, Any]: ...


def account_is_ready(account: dict[str, Any]) -> bool:
    """A seller can be paid once Stripe allows transfers to their account
    (an Account as a plain dict, e.g. from a webhook)."""
    capabilities = account.get("capabilities") or {}
    return bool(account.get("details_submitted")) and capabilities.get("transfers") == "active"


class StripeGateway:
    def __init__(self, secret_key: str, webhook_secret: str):
        self._client = stripe.StripeClient(secret_key, http_client=stripe.HTTPXClient())
        self._webhook_secret = webhook_secret

    async def _call(self, coro: Any) -> Any:
        try:
            return await coro
        except stripe.StripeError as e:
            message = getattr(e, "user_message", None) or "The payment provider refused the request"
            raise StripeFailed(message) from e

    async def create_account(self, user_id: str, email: str | None) -> str:
        # Any: the SDK's TypedDicts don't accept a dict built up in steps.
        params: Any = {
            "type": "express",
            "capabilities": {"transfers": {"requested": True}},
            "metadata": {"user_id": user_id},
        }
        if email:
            params["email"] = email
        account = await self._call(self._client.v1.accounts.create_async(
            params, {"idempotency_key": f"account-{user_id}"}))
        return str(account.id)

    async def onboarding_link(self, account_id: str, refresh_url: str, return_url: str) -> str:
        link = await self._call(self._client.v1.account_links.create_async({
            "account": account_id, "type": "account_onboarding",
            "refresh_url": refresh_url, "return_url": return_url,
        }))
        return str(link.url)

    async def dashboard_link(self, account_id: str) -> str:
        link = await self._call(self._client.v1.accounts.login_links.create_async(account_id))
        return str(link.url)

    async def account_ready(self, account_id: str) -> bool:
        account = await self._call(self._client.v1.accounts.retrieve_async(account_id))
        return account_is_ready(account.to_dict())

    async def create_checkout(
        self, checkout_id: str, lines: list[CheckoutLine], currency: str, expires_at: datetime,
        success_url: str, cancel_url: str, customer_email: str | None,
    ) -> HostedCheckout:
        params: Any = {
            "mode": "payment",
            "payment_method_types": ["card"],
            "line_items": [{
                "quantity": 1,
                "price_data": {
                    "currency": currency, "unit_amount": line.amount_cents,
                    "product_data": {"name": line.name, "description": line.description},
                },
            } for line in lines],
            "client_reference_id": checkout_id,
            "metadata": {"checkout_id": checkout_id},
            "payment_intent_data": {"transfer_group": checkout_id, "metadata": {"checkout_id": checkout_id}},
            "expires_at": int(expires_at.timestamp()),
            "success_url": success_url,
            "cancel_url": cancel_url,
        }
        if customer_email:
            params["customer_email"] = customer_email
        session = await self._call(self._client.v1.checkout.sessions.create_async(
            params, {"idempotency_key": f"checkout-{checkout_id}"}))
        return HostedCheckout(session_id=str(session.id), url=str(session.url))

    async def expire_checkout(self, session_id: str) -> str:
        """Close a payment page. Returns its status afterwards: "expired",
        or "complete" if the buyer already paid."""
        try:
            await self._client.v1.checkout.sessions.expire_async(session_id)
            return "expired"
        except stripe.InvalidRequestError:
            # Only open pages can be expired; find out what it is instead.
            session = await self._call(self._client.v1.checkout.sessions.retrieve_async(session_id))
            return str(session.status)

    async def charge_id(self, payment_intent_id: str) -> str:
        intent = await self._call(self._client.v1.payment_intents.retrieve_async(payment_intent_id))
        return str(intent.latest_charge)

    async def transfer(self, order_id: str, amount_cents: int, currency: str, account_id: str,
                       charge_id: str, transfer_group: str) -> str:
        transfer = await self._call(self._client.v1.transfers.create_async({
            "amount": amount_cents, "currency": currency, "destination": account_id,
            # Funded by the buyer's charge, so it doesn't wait for the
            # platform balance to become available.
            "source_transaction": charge_id,
            "transfer_group": transfer_group,
            "metadata": {"order_id": order_id},
        }, {"idempotency_key": f"transfer-{order_id}"}))
        return str(transfer.id)

    async def reverse_transfer(self, key: str, transfer_id: str, amount_cents: int) -> None:
        await self._call(self._client.v1.transfers.reversals.create_async(
            transfer_id, {"amount": amount_cents, "metadata": {"refund": key}},
            {"idempotency_key": f"reversal-{key}"}))

    async def refund(self, key: str, payment_intent_id: str, amount_cents: int) -> str:
        refund = await self._call(self._client.v1.refunds.create_async({
            "payment_intent": payment_intent_id, "amount": amount_cents, "metadata": {"refund": key},
        }, {"idempotency_key": f"refund-{key}"}))
        return str(refund.id)

    def parse_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        if not self._webhook_secret:
            raise PaymentsUnavailable("Stripe webhooks aren't configured on this server")
        event = stripe.Webhook.construct_event(payload, signature, self._webhook_secret)
        return event.to_dict()


@lru_cache(maxsize=1)
def _stripe_gateway() -> StripeGateway:
    return StripeGateway(settings.stripe_secret_key, settings.stripe_webhook_secret)


# Tests install a fake here (use_gateway) instead of talking to Stripe.
_override: Gateway | None = None


def use_gateway(gateway: Gateway | None) -> None:
    global _override
    _override = gateway


def get_gateway() -> Gateway:
    if _override is not None:
        return _override
    if not settings.stripe_secret_key:
        raise PaymentsUnavailable("Payments aren't configured on this server")
    return _stripe_gateway()
