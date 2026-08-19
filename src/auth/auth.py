from typing import Any

import jwt
from fastapi import HTTPException, Header
from jwt import PyJWKClient
from sqlalchemy import Table, Column, MetaData
from sqlalchemy.dialects.postgresql import UUID

from models.database import Base
from utils.settings import Settings, settings

def get_current_user(authorization: str = Header(...)) -> dict:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1]
    try:
        return _verify_supabase_jwt(token, settings)
    except jwt.PyJWTError as e:
        raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

def _verify_supabase_jwt(token: str, supabase_settings: Settings) -> dict:
    jwks_url = f"https://{settings.supabase_project_ref}.supabase.co/auth/v1/.well-known/jwks.json"
    jwks_client = PyJWKClient(jwks_url)
    signing_key = jwks_client.get_signing_key_from_jwt(token)

    payload = jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256"],
        audience="authenticated",
        issuer=f"https://{supabase_settings.supabase_project_ref}.supabase.co/auth/v1",
    )
    return payload


auth_users = Table(
    "users",
    Base.metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    schema="auth",
)