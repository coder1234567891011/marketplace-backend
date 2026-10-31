"""
Queueing emails. queue_email() adds a row to the emails table in the
caller's transaction, so an email exists exactly when what it's about was
committed. Once that transaction commits, the queued emails are sent in the
background (services/email/sender.py); anything not sent then, e.g. after a
restart or a provider outage, is retried by scripts/send_emails.py.
"""
import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from models.database import Email
from services.email.templates import TEMPLATES
from utils.settings import settings

log = logging.getLogger(__name__)

_QUEUED = "emails_queued"
# Running background sends, kept so they aren't garbage collected mid-send.
_tasks: set[asyncio.Task[None]] = set()


def email_configured() -> bool:
    return bool(settings.resend_api_key and settings.email_from)


def queue_email(session: AsyncSession, user_id: uuid.UUID, template: str, **data: Any) -> None:
    if template not in TEMPLATES:
        raise ValueError(f"Unknown email template {template}")
    session.add(Email(user_id=user_id, template=template, data=data))
    session.info[_QUEUED] = True


async def _send_now() -> None:
    from services.email.sender import send_pending
    from services.persistence.database import get_sessionmaker

    try:
        async with get_sessionmaker()() as session, session.begin():
            await send_pending(session)
    except Exception:
        log.exception("Sending queued emails failed; the nightly run will retry")


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    if not session.info.pop(_QUEUED, False) or not email_configured():
        return
    try:
        task = asyncio.get_running_loop().create_task(_send_now())
    except RuntimeError:  # no event loop, e.g. a sync script
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop(_QUEUED, None)
