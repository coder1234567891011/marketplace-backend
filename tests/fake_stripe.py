"""An in-memory stand-in for services/payments/stripe_gateway.StripeGateway.

It records what the API asked Stripe to do, and builds webhook events the
way Stripe would send them. Webhooks are "signed" with the signature "valid".
"""
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from services.payments.stripe_gateway import CheckoutLine, HostedCheckout, StripeFailed


@dataclass
class FakeSession:
    checkout_id: str
    lines: list[CheckoutLine]
    expires_at: datetime
    status: str = "open"


@dataclass
class FakeStripe:
    accounts: dict[str, bool] = field(default_factory=dict)  # account id → ready
    sessions: dict[str, FakeSession] = field(default_factory=dict)
    transfers: dict[str, dict[str, Any]] = field(default_factory=dict)  # order id → transfer
    reversed: list[str] = field(default_factory=list)
    refunds: dict[str, int] = field(default_factory=dict)  # idempotency id → amount
    fail_transfers: bool = False

    async def create_account(self, user_id: str, email: str | None) -> str:
        account_id = f"acct_{user_id[:8]}"
        self.accounts.setdefault(account_id, False)
        return account_id

    async def onboarding_link(self, account_id: str, refresh_url: str, return_url: str) -> str:
        return f"https://connect.stripe.test/setup/{account_id}?return={return_url}"

    async def dashboard_link(self, account_id: str) -> str:
        return f"https://connect.stripe.test/express/{account_id}"

    async def account_ready(self, account_id: str) -> bool:
        return self.accounts.get(account_id, False)

    async def create_checkout(
        self, checkout_id: str, lines: list[CheckoutLine], currency: str, expires_at: datetime,
        success_url: str, cancel_url: str, customer_email: str | None,
    ) -> HostedCheckout:
        session_id = f"cs_test_{checkout_id[:8]}"
        self.sessions[session_id] = FakeSession(checkout_id, lines, expires_at)
        return HostedCheckout(session_id, f"https://checkout.stripe.test/{session_id}")

    async def expire_checkout(self, session_id: str) -> str:
        session = self.sessions[session_id]
        if session.status == "open":
            session.status = "expired"
        return session.status

    async def charge_id(self, payment_intent_id: str) -> str:
        return payment_intent_id.replace("pi_", "ch_")

    async def transfer(self, order_id: str, amount_cents: int, currency: str, account_id: str,
                       charge_id: str, transfer_group: str) -> str:
        if self.fail_transfers:
            raise StripeFailed("Your destination account needs to have at least one of the following capabilities "
                               "enabled: transfers")
        self.transfers.setdefault(order_id, {"id": f"tr_{order_id[:8]}", "amount": amount_cents,
                                             "destination": account_id, "source_transaction": charge_id})
        return str(self.transfers[order_id]["id"])

    async def reverse_transfer(self, order_id: str, transfer_id: str) -> None:
        self.reversed.append(transfer_id)

    async def refund(self, order_id: str, payment_intent_id: str, amount_cents: int) -> str:
        self.refunds[order_id] = amount_cents
        return f"re_{order_id[:8]}"

    def parse_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        if signature != "valid":
            raise ValueError("bad signature")
        return dict(json.loads(payload))

    # Building events --------------------------------------------------
    def session_for(self, checkout_id: str) -> tuple[str, FakeSession]:
        return next((sid, s) for sid, s in self.sessions.items() if s.checkout_id == checkout_id)

    def completed_event(self, checkout_id: str, event_id: str | None = None) -> dict[str, Any]:
        session_id, session = self.session_for(checkout_id)
        session.status = "complete"
        return {"id": event_id or f"evt_paid_{checkout_id[:8]}", "type": "checkout.session.completed",
                "data": {"object": {"id": session_id, "payment_status": "paid",
                                    "payment_intent": f"pi_{checkout_id[:8]}",
                                    "metadata": {"checkout_id": checkout_id}}}}

    def expired_event(self, checkout_id: str) -> dict[str, Any]:
        session_id, session = self.session_for(checkout_id)
        session.status = "expired"
        return {"id": f"evt_expired_{checkout_id[:8]}", "type": "checkout.session.expired",
                "data": {"object": {"id": session_id, "metadata": {"checkout_id": checkout_id}}}}
