"""
Photo storage in an S3-compatible bucket (Cloudflare R2, Supabase Storage,
AWS S3, …), used only through presigned URLs: the app uploads straight to the
bucket and shows photos straight from it, so image bytes never pass through
the API.

URLs are signed with AWS Signature Version 4 (query-string auth), computed
here rather than with boto3 to keep the image small. Path-style URLs
(endpoint/bucket/key) work with every provider above.
"""
import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

from utils.errors import AppError
from utils.settings import settings


class StorageUnavailable(AppError):
    status_code = 503
    code = "storage_unavailable"


@dataclass
class Upload:
    url: str
    # Send exactly these headers with the PUT; they're part of the signature.
    headers: dict[str, str]
    expires_at: datetime


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def presign(
    method: str, url: str, *, region: str, access_key_id: str, secret_access_key: str, now: datetime,
    expires_seconds: int, headers: dict[str, str] | None = None, service: str = "s3",
) -> str:
    """A SigV4 presigned URL. headers (besides host) must be sent unchanged with the request."""
    parts = urlsplit(url)
    signed_headers = {"host": parts.netloc, **{k.lower(): v.strip() for k, v in (headers or {}).items()}}
    names = sorted(signed_headers)
    path = quote(parts.path or "/", safe="/-_.~")
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    scope = f"{stamp[:8]}/{region}/{service}/aws4_request"
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key_id}/{scope}",
        "X-Amz-Date": stamp,
        "X-Amz-Expires": str(expires_seconds),
        "X-Amz-SignedHeaders": ";".join(names),
    }
    canonical_query = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(query.items()))
    canonical_request = "\n".join([
        method, path, canonical_query,
        "".join(f"{n}:{signed_headers[n]}\n" for n in names), ";".join(names), "UNSIGNED-PAYLOAD",
    ])
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical_request.encode()).hexdigest()])
    key = f"AWS4{secret_access_key}".encode()
    for part in (stamp[:8], region, service, "aws4_request"):
        key = _hmac(key, part)
    signature = hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{parts.scheme}://{parts.netloc}{path}?{canonical_query}&X-Amz-Signature={signature}"


def is_configured() -> bool:
    return bool(settings.storage_endpoint and settings.storage_bucket and settings.storage_access_key_id
                and settings.storage_secret_access_key)


def _sign(method: str, key: str, expires: timedelta, now: datetime, headers: dict[str, str] | None = None) -> str:
    if not is_configured():
        raise StorageUnavailable("Photo uploads aren't set up on this server")
    url = f"{settings.storage_endpoint.rstrip('/')}/{settings.storage_bucket}/{key}"
    return presign(method, url, region=settings.storage_region, access_key_id=settings.storage_access_key_id,
                   secret_access_key=settings.storage_secret_access_key, now=now,
                   expires_seconds=int(expires.total_seconds()), headers=headers)


def upload(key: str, content_type: str, size_bytes: int, now: datetime) -> Upload:
    """A URL the app can PUT exactly this file to."""
    expires = timedelta(seconds=settings.storage_upload_seconds)
    headers = {"Content-Type": content_type, "Content-Length": str(size_bytes)}
    return Upload(url=_sign("PUT", key, expires, now, headers), headers={"Content-Type": content_type},
                  expires_at=now + expires)


def download_url(key: str, now: datetime) -> str | None:
    """A temporary link to view a stored photo, or None when storage isn't set up."""
    if not is_configured():
        return None
    return _sign("GET", key, timedelta(seconds=settings.storage_download_seconds), now)
