"""Demo write guard — per-visitor rate limit for a publicly reachable demo (MC 2034.2).

This app is published on a public URL (Cloudflare Tunnel -> nginx -> 127.0.0.1), so every
mutating endpoint is world-reachable. This middleware keeps a demo a demo: a token bucket
per visitor, applied ONLY to mutating methods, so browsing stays unthrottled.

Visitor identity: Cloudflare sets ``CF-Connecting-IP`` at the tunnel and nginx adds
``X-Forwarded-For`` ($proxy_add_x_forwarded_for, real client first). ``request.client.host``
is always 127.0.0.1 behind this proxy chain, so it is a last resort only — without the
headers every visitor would share one bucket.

No third-party dependencies: the render pipeline builds a venv per app from requirements.txt,
and a rate limiter is not worth a new pinned dependency.
"""
from __future__ import annotations

import time
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CAPACITY = 10           # burst a visitor may make back-to-back
WINDOW_SECONDS = 300.0  # bucket refills fully over this window
MAX_TRACKED = 5000      # bound memory under a distributed flood


def visitor_ip(request: Request) -> str:
    """Best available identity for the real visitor, not the proxy."""
    cf = (request.headers.get("cf-connecting-ip") or "").strip()
    if cf:
        return cf
    xff = (request.headers.get("x-forwarded-for") or "").strip()
    if xff:
        return xff.split(",")[0].strip()
    client = request.client
    return client.host if client else "unknown"


class DemoWriteGuard(BaseHTTPMiddleware):
    """Token-bucket rate limit on mutating requests, keyed per visitor."""

    def __init__(self, app, capacity: int = CAPACITY, window: float = WINDOW_SECONDS) -> None:
        super().__init__(app)
        self._capacity = float(capacity)
        self._rate = float(capacity) / float(window)
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = Lock()

    def _prune_locked(self) -> None:
        # Drop buckets that have refilled (idle visitors carry no state worth keeping).
        # Only if that is not enough do we reset wholesale — a blanket clear would hand an
        # attacker a way to wipe everyone's limit, so it is the last resort, not the first.
        self._buckets = {
            key: val for key, val in self._buckets.items() if val[0] < self._capacity - 0.001
        }
        if len(self._buckets) > MAX_TRACKED:
            self._buckets.clear()

    def _take(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if len(self._buckets) > MAX_TRACKED:
                self._prune_locked()
            tokens, last = self._buckets.get(key, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - last) * self._rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                return False
            self._buckets[key] = (tokens - 1.0, now)
            return True

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method in WRITE_METHODS and not self._take(visitor_ip(request)):
            return JSONResponse(
                {
                    "detail": (
                        "För många ändringar från samma besökare. "
                        "Det här är en demo — vänta en stund och försök igen."
                    )
                },
                status_code=429,
                headers={"Retry-After": str(int(WINDOW_SECONDS))},
            )
        return await call_next(request)
