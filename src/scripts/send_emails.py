"""
Send queued emails that weren't sent right after their request (a restart,
or Resend being down); see services/email.

Run from src/ (the nightly GitHub Actions job runs it, see
.github/workflows/scryfall-sync.yml):
    python -m scripts.send_emails
"""
import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from services.email.sender import send_pending
from services.persistence.database import create_engine
from utils.settings import settings


async def main() -> None:
    engine = create_engine(settings.database_url, pool_size=1, max_overflow=0)
    try:
        # One pass: an email that fails now is tried again tomorrow, not in a loop.
        async with async_sessionmaker(engine)() as session, session.begin():
            result = await send_pending(session, limit=2000)
    finally:
        await engine.dispose()
    print(f"Emails: {result.sent} sent, {result.failed} failed, {result.skipped} skipped")

if __name__ == "__main__":
    asyncio.run(main())
