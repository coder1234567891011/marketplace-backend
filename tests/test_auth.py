import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import ASGITransport, AsyncClient

import auth.auth
from app import app

ISSUER = f"https://{auth.auth.settings.supabase_project_ref}.supabase.co/auth/v1"


class _FakeJWKClient:
    def __init__(self, key: ec.EllipticCurvePrivateKey):
        self.key = key.public_key()

    def get_signing_key_from_jwt(self, token: str):  # type: ignore[no-untyped-def]
        return self


@pytest.fixture
def signing_key(monkeypatch: pytest.MonkeyPatch) -> ec.EllipticCurvePrivateKey:
    key = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setattr(auth.auth, "_jwks_client", lambda: _FakeJWKClient(key))
    return key


def _token(key: ec.EllipticCurvePrivateKey, **overrides: object) -> str:
    claims = {"sub": str(uuid.uuid4()), "aud": "authenticated", "iss": ISSUER,
              "exp": int(time.time()) + 60, "email": "a@example.com", **overrides}
    return jwt.encode(claims, key, algorithm="ES256")


def test_valid_token_is_accepted(signing_key: ec.EllipticCurvePrivateKey) -> None:
    user_id = uuid.uuid4()
    user = auth.auth.get_current_user(f"Bearer {_token(signing_key, sub=str(user_id))}")
    assert user.id == user_id
    assert user.email == "a@example.com"


@pytest.mark.parametrize("overrides", [
    {"aud": "anon"},
    {"iss": "https://evil.example.com/auth/v1"},
    {"exp": int(time.time()) - 10},
    {"sub": "not-a-uuid"},
])
def test_bad_tokens_are_rejected(signing_key: ec.EllipticCurvePrivateKey, overrides: dict) -> None:
    with pytest.raises(auth.auth.Unauthorized):
        auth.auth.get_current_user(f"Bearer {_token(signing_key, **overrides)}")


def test_token_signed_by_another_key_is_rejected(signing_key: ec.EllipticCurvePrivateKey) -> None:
    other = ec.generate_private_key(ec.SECP256R1())
    with pytest.raises(auth.auth.Unauthorized):
        auth.auth.get_current_user(f"Bearer {_token(other)}")


async def test_missing_or_malformed_header_returns_401_before_touching_db() -> None:
    # No database is configured here, so this also proves auth runs before a session is opened.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for headers in ({}, {"Authorization": "Token abc"}):
            resp = await client.get("/v1/users/me", headers=headers)
            assert resp.status_code == 401
            assert resp.json()["error"]["code"] == "unauthorized"


def test_jwks_client_is_reused() -> None:
    auth.auth._jwks_client.cache_clear()
    assert auth.auth._jwks_client() is auth.auth._jwks_client()
