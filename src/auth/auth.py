import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated

import jwt
from fastapi import Depends, Header, Request
from jwt import PyJWKClient
from sqlalchemy import select

from models.database import AccountStatus, User, auth_users
from services.persistence.database import SessionDep
from utils.errors import Forbidden, Unauthorized
from utils.settings import settings


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    email: str | None = None


@lru_cache(maxsize=1)
def _jwks_client() -> PyJWKClient:
    # One client per process: PyJWKClient caches the key set, so keys are
    # fetched from Supabase once (and on rotation) instead of on every request.
    jwks_url = f"https://{settings.supabase_project_ref}.supabase.co/auth/v1/.well-known/jwks.json"
    return PyJWKClient(jwks_url, cache_keys=True, lifespan=3600)


def verify_token(token: str) -> dict:
    signing_key = _jwks_client().get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256"],
        audience="authenticated",
        issuer=f"https://{settings.supabase_project_ref}.supabase.co/auth/v1",
    )


def _user_from_header(authorization: str) -> CurrentUser:
    if not authorization.startswith("Bearer "):
        raise Unauthorized("Missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        payload = verify_token(token)
        return CurrentUser(id=uuid.UUID(payload["sub"]), email=payload.get("email"))
    except (jwt.PyJWTError, KeyError, ValueError) as e:
        raise Unauthorized("Invalid token") from e


def get_current_user(authorization: Annotated[str | None, Header()] = None) -> CurrentUser:
    if authorization is None:
        raise Unauthorized("Missing bearer token")
    return _user_from_header(authorization)


def get_optional_user(authorization: Annotated[str | None, Header()] = None) -> CurrentUser | None:
    """For public endpoints that show more to the owner (e.g. private collections)."""
    if authorization is None:
        return None
    return _user_from_header(authorization)


_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


async def get_active_user(
    request: Request,
    current: Annotated[CurrentUser, Depends(get_current_user)],
    session: SessionDep,
) -> CurrentUser:
    """A verified token whose owner may use the API right now.

    Checked against the database on every request (one primary-key lookup),
    so confirming an email or banning someone takes effect immediately
    instead of when their token expires.
      * email not confirmed → 403 email_unconfirmed
      * banned              → 403 account_banned (everything)
      * suspended           → 403 account_suspended (writes only; reads still work)
    Users without a profile row yet pass, so they can create one.
    """
    row = (await session.execute(
        select(auth_users.c.email_confirmed_at, User.account_status)
        .select_from(auth_users)
        .outerjoin(User, User.id == auth_users.c.id)
        .where(auth_users.c.id == current.id)
    )).one_or_none()
    if row is None:
        raise Unauthorized("Unknown user")
    email_confirmed_at, account_status = row
    if email_confirmed_at is None:
        raise Forbidden("Confirm your email address to continue", code="email_unconfirmed")
    if account_status == AccountStatus.banned:
        raise Forbidden("This account has been banned", code="account_banned")
    if account_status == AccountStatus.suspended and request.method not in _READ_METHODS:
        raise Forbidden("This account is suspended", code="account_suspended")
    return current


AuthUser = Annotated[CurrentUser, Depends(get_active_user)]


async def get_admin_user(current: AuthUser, session: SessionDep) -> CurrentUser:
    """An active user who is a moderator (users.is_admin)."""
    if not await session.scalar(select(User.is_admin).where(User.id == current.id)):
        raise Forbidden("Only moderators can do this", code="not_moderator")
    return current


AdminUser = Annotated[CurrentUser, Depends(get_admin_user)]
OptionalUser = Annotated[CurrentUser | None, Depends(get_optional_user)]

