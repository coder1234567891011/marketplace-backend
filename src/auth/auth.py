import jwt
from jwt import PyJWKClient

from utils.settings import Settings

def verify_supabase_jwt(token: str, settings: Settings) -> dict:
    jwks_url = f"https://{settings.supabase_project_ref}.supabase.co/auth/v1/.well-known/jwks.json"
    jwks_client = PyJWKClient(jwks_url)
    signing_key = jwks_client.get_signing_key_from_jwt(token)

    payload = jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256"],
        audience="authenticated",
        issuer=f"https://{settings.supabase_project_ref}.supabase.co/auth/v1",
    )
    return payload