"""The platform's fee on each seller order, from the terms in settings."""
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from utils.settings import settings


@dataclass(frozen=True)
class FeeTerms:
    percent: Decimal
    fixed_cents: int
    min_cents: int

    @classmethod
    def current(cls) -> "FeeTerms":
        return cls(settings.platform_fee_percent, settings.platform_fee_fixed_cents, settings.platform_fee_min_cents)

    def fee_cents(self, subtotal_cents: int, shipping_cents: int) -> int:
        """Percent of the cards' price plus the fixed amount, at least the
        minimum, and never more than the order total."""
        percent = (Decimal(subtotal_cents) * self.percent / 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        fee = max(int(percent) + self.fixed_cents, self.min_cents)
        return min(fee, subtotal_cents + shipping_cents)
