"""
Auto-deliver old shipped orders and pay sellers that are due
(services/orders/maintenance.py).

Run from src/ (the nightly GitHub Actions job runs it, see
.github/workflows/scryfall-sync.yml):
    python -m scripts.order_maintenance

Safe to re-run: payouts are idempotent per order.
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from services.orders.maintenance import run_maintenance
from services.persistence.database import create_engine
from utils.settings import settings


async def main() -> None:
    engine = create_engine(settings.database_url, pool_size=1, max_overflow=0)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            result = await run_maintenance(session, datetime.now(timezone.utc))
    finally:
        await engine.dispose()
    print(f"Orders: {result.delivered} auto-delivered, {result.paid_out} paid out, "
          f"{result.payouts_failed} payouts failed")
    if result.payouts_failed:
        raise SystemExit(1)  # fails the workflow run, so someone looks


if __name__ == "__main__":
    asyncio.run(main())
