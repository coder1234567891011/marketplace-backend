"""
Recompute marketplace prices from recent sales (services/prices/market.py).

Run from src/ (the nightly GitHub Actions job runs it after the Scryfall
sync, see .github/workflows/scryfall-sync.yml):
    python -m scripts.market_prices

Safe to re-run: it rebuilds market_prices in one transaction, and a second
run on the same day only updates that day's snapshots.
"""
import asyncio
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from services.persistence.database import create_engine
from services.prices.market import recompute_market_prices
from utils.settings import settings


async def main() -> None:
    engine = create_engine(settings.database_url, pool_size=1, max_overflow=0)
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            result = await recompute_market_prices(session, datetime.now(timezone.utc))
    finally:
        await engine.dispose()
    print(f"Market prices: {result.from_sales} from sales, {result.from_listings} from listings; "
          f"{result.snapshots} snapshots written")


if __name__ == "__main__":
    asyncio.run(main())
