# ----------------------------------------------------------------
# REPORTS AND MODERATION
# ----------------------------------------------------------------
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from models.database import AccountStatus, ModerationActionType, ReportReason, ReportStatus, ReportTarget
from models.model import _ORMBase


class ReportCreate(BaseModel):
    target_type: ReportTarget
    target_id: uuid.UUID
    reason: ReportReason
    details: str | None = Field(default=None, max_length=4000)


class ReportRead(_ORMBase, ReportCreate):
    id: uuid.UUID
    status: ReportStatus
    resolved_at: datetime | None
    created_at: datetime


class ReportAdminRead(ReportRead):
    reporter_id: uuid.UUID
    resolved_by: uuid.UUID | None
    resolution_note: str | None


class ReportResolve(BaseModel):
    status: Literal[ReportStatus.actioned, ReportStatus.dismissed]
    note: str | None = Field(default=None, max_length=4000)


class ModerationNote(BaseModel):
    # Why; kept in the moderation log.
    note: str | None = Field(default=None, max_length=4000)


class AccountStatusUpdate(ModerationNote):
    account_status: AccountStatus


class IssueDecision(ModerationNote):
    # What to refund the buyer (0 decides for the seller); at most what's
    # left of the order. The note is also posted to the issue.
    refund_cents: int = Field(ge=0)


class ModerationActionRead(_ORMBase):
    id: uuid.UUID
    admin_id: uuid.UUID
    action: ModerationActionType
    target_id: uuid.UUID
    details: dict[str, Any]
    note: str | None
    created_at: datetime
