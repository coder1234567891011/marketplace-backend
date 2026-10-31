"""
Sending queued emails through Resend's HTTP API (https://resend.com/docs/api-reference/emails/send-email).

Rows are claimed with FOR UPDATE SKIP LOCKED, so the background send after
a request and the nightly retry never pick up the same email, and each
request to Resend carries the email's id as its idempotency key, so a send
that's retried after a timeout isn't delivered twice.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.database import Email, EmailStatus, auth_users
from services.email.outbox import email_configured
from services.email.templates import render
from utils.settings import settings

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


@dataclass
class SendResult:
    sent: int = 0
    failed: int = 0    # will be retried, unless out of attempts
    skipped: int = 0


async def send_pending(session: AsyncSession, client: httpx.AsyncClient | None = None,
                       limit: int = 100) -> SendResult:
    rows = (await session.execute(
        select(Email, auth_users.c.email)
        .outerjoin(auth_users, auth_users.c.id == Email.user_id)
        .where(Email.status == EmailStatus.pending)
        .order_by(Email.created_at).limit(limit)
        .with_for_update(of=Email, skip_locked=True)
    )).all()
    result = SendResult()
    if not rows:
        return result
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=15)
    try:
        for email, address in rows:
            if not email_configured() or not address:
                email.status = EmailStatus.skipped
                result.skipped += 1
                continue
            subject, body = render(email.template, email.data)
            email.attempts += 1
            try:
                resp = await http.post(RESEND_URL, json={
                    "from": settings.email_from, "to": [address], "subject": subject, "text": body,
                }, headers={"Authorization": f"Bearer {settings.resend_api_key}",
                            "Idempotency-Key": str(email.id)})
                resp.raise_for_status()
            except httpx.HTTPError as e:
                detail = e.response.text[:500] if isinstance(e, httpx.HTTPStatusError) else repr(e)
                email.last_error = detail
                if email.attempts >= settings.email_max_attempts:
                    email.status = EmailStatus.failed
                    log.error("Giving up on email %s (%s): %s", email.id, email.template, detail)
                result.failed += 1
                continue
            email.status, email.sent_at, email.last_error = EmailStatus.sent, datetime.now(timezone.utc), None
            result.sent += 1
    finally:
        if owns_client:
            await http.aclose()
    await session.flush()
    return result
