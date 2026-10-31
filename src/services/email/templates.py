"""
Email templates: plain text, one function per template taking the data
queued with it (services/email/outbox.queue_email) and returning
(subject, body). Links go to the web app (settings.frontend_url).
"""
from collections.abc import Callable
from typing import Any

from utils.settings import settings

Rendered = tuple[str, str]


def money(cents: int, currency: str = "usd") -> str:
    symbol = {"usd": "$", "eur": "€", "gbp": "£"}.get(currency, "")
    amount = f"{cents / 100:.2f}"
    return f"{symbol}{amount}" if symbol else f"{amount} {currency.upper()}"


def _link(path: str) -> str:
    return f"{settings.frontend_url.rstrip('/')}{path}"


def _order_link(d: dict[str, Any]) -> str:
    return _link(f"/orders/{d['order_id']}")


def order_placed(d: dict[str, Any]) -> Rendered:
    sellers = "the seller" if d["sellers"] == 1 else f"each of the {d['sellers']} sellers"
    return ("Your order is confirmed",
            f"Thanks for your order. You paid {money(d['total_cents'], d['currency'])}, and {sellers} will "
            f"ship your cards soon.\n\n{_link('/orders')}")


def order_sold(d: dict[str, Any]) -> Rendered:
    return (f"You sold cards: order {d['order']}",
            f"A buyer paid {money(d['total_cents'], d['currency'])} for order {d['order']}. Pack the cards "
            f"and mark the order shipped.\n\n{_order_link(d)}")


def order_shipped(d: dict[str, Any]) -> Rendered:
    tracking = f"Tracking number: {d['tracking_number']}\n\n" if d.get("tracking_number") else ""
    return (f"Order {d['order']} has shipped",
            f"Your order is on its way.\n\n{tracking}Mark it received when it arrives.\n\n{_order_link(d)}")


def order_refunded(d: dict[str, Any]) -> Rendered:
    return (f"Refund for order {d['order']}",
            f"You've been refunded {money(d['refund_cents'], d['currency'])} for order {d['order']}. "
            f"It can take 5-10 days to reach your account.\n\n{_order_link(d)}")


def issue_opened(d: dict[str, Any]) -> Rendered:
    return (f"Problem reported with order {d['order']}",
            f"The buyer reported a problem with order {d['order']}: {d['reason']}. Reply or refund within "
            f"{d['seller_days']} days, or the buyer can ask the marketplace to step in.\n\n{_order_link(d)}")


def issue_message(d: dict[str, Any]) -> Rendered:
    return (f"New message about order {d['order']}", f"{d['body']}\n\n{_order_link(d)}")


def issue_escalated(d: dict[str, Any]) -> Rendered:
    return (f"The marketplace is reviewing order {d['order']}",
            f"The problem with order {d['order']} has been passed to our team, who will decide it.\n\n"
            f"{_order_link(d)}")


def issue_resolved(d: dict[str, Any]) -> Rendered:
    outcome = {"refunded": f"refunded {money(d['refund_cents'], d['currency'])} to the buyer",
               "no_refund": "closed without a refund",
               "withdrawn": "withdrawn by the buyer"}[d["resolution"]]
    note = f"\n\n{d['note']}" if d.get("note") else ""
    return (f"Problem with order {d['order']} resolved",
            f"The problem with order {d['order']} was {outcome}.{note}\n\n{_order_link(d)}")


def account_status(d: dict[str, Any]) -> Rendered:
    text = {"active": "Your account has been reinstated.",
            "suspended": "Your account has been suspended. You can still sign in and see your orders, "
                         "but can't buy, sell or make changes.",
            "banned": "Your account has been banned."}[d["account_status"]]
    note = f"\n\nReason: {d['note']}" if d.get("note") else ""
    return "Your marketplace account", f"{text}{note}"


TEMPLATES: dict[str, Callable[[dict[str, Any]], Rendered]] = {
    f.__name__: f for f in (order_placed, order_sold, order_shipped, order_refunded, issue_opened,
                            issue_message, issue_escalated, issue_resolved, account_status)
}


def render(template: str, data: dict[str, Any]) -> Rendered:
    return TEMPLATES[template](data)
