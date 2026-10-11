"""Small in-process sliding-window rate limiter (per client IP and bucket).

Single-instance by design (free-tier deploy). For multiple instances back it with Redis.
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from .config import settings


class RateLimiter:
    def __init__(self) -> None:
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_gc = time.monotonic()

    def check(self, key: str, limit: int, window: float = 60.0) -> float:
        """Return 0 if allowed, else seconds until the client may retry."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return max(1.0, window - (now - q[0]))
            q.append(now)
            if now - self._last_gc > 300:  # bound memory under many distinct IPs
                self._last_gc = now
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > window]:
                    del self._hits[k]
            return 0.0


limiter = RateLimiter()


def client_ip(request) -> str:
    if settings.trust_proxy:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[-1].strip()  # last hop = address seen by our own proxy, not client-spoofable
    return request.client.host if request.client else "unknown"
