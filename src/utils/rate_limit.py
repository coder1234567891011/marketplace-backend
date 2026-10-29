"""
In-memory sliding-window rate limiting.

Two layers:
  * RateLimitMiddleware: a per-IP ceiling on every request, so one client
    can't flood the API or the database.
  * rate_limit(...): per-user limits on specific write endpoints (profile
    creation, listings, ratings, ...), applied after authentication so a
    forged token can't use up someone else's allowance.

Counters live in process memory: they reset on deploy and are per machine.
That's fine while Fly runs a single machine; with several machines, move
the counters to a shared store such as Redis.
"""
import math
import time
from collections import deque
from typing import Any

from fastapi import Depends
from starlette.types import ASGIApp, Receive, Scope, Send

from auth.auth import AuthUser
from utils.errors import TooManyRequests, error_response
from utils.settings import settings


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = {}
        self._calls = 0

    def hit(self, key: str, limit: int, window_seconds: float) -> float | None:
        """Record a request. Returns seconds to wait if over the limit, else None."""
        now = time.monotonic()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return hits[0] + window_seconds - now
        hits.append(now)
        self._calls += 1
        if self._calls % 10_000 == 0:
            self._sweep(now)
        return None

    def _sweep(self, now: float, max_window: float = 24 * 3600) -> None:
        # Drop keys that have had no traffic for longer than any window we use.
        stale = [k for k, h in self._hits.items() if not h or h[-1] <= now - max_window]
        for k in stale:
            del self._hits[k]

    def reset(self) -> None:
        self._hits.clear()


limiter = RateLimiter()


def client_ip(scope: Scope) -> str:
    # On Fly, the proxy sets Fly-Client-IP to the real client address
    # (overwriting any value the client sent).
    for name, value in scope.get("headers", []):
        if name == b"fly-client-ip":
            return value.decode()
    client = scope.get("client")
    return client[0] if client else "unknown"


class RateLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and settings.rate_limit_enabled and scope["path"] != "/health":
            retry_after = limiter.hit(f"ip:{client_ip(scope)}", settings.rate_limit_per_ip_per_minute, 60)
            if retry_after is not None:
                response = error_response(429, "rate_limited", "Too many requests",
                                          headers={"Retry-After": str(math.ceil(retry_after))})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def rate_limit(bucket: str, limit: int, window_seconds: int) -> Any:
    """Per-user limit for a route: @router.post(..., dependencies=[rate_limit("ratings", 30, 3600)])."""

    async def dependency(current: AuthUser) -> None:
        if not settings.rate_limit_enabled:
            return
        retry_after = limiter.hit(f"{bucket}:{current.id}", limit, window_seconds)
        if retry_after is not None:
            raise TooManyRequests(f"Rate limit exceeded for {bucket}",
                                  headers={"Retry-After": str(math.ceil(retry_after))})

    return Depends(dependency)
