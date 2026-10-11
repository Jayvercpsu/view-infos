"""Password hashing, session signing, CSRF origin checks, and rate limiting."""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

logger = logging.getLogger("view-info.security")

SESSION_COOKIE_NAME = "surprise_session"
SESSION_SALT = "view-info.session.v1"
SESSION_VERSION = 1
# bcrypt only reads the first 72 bytes of a password; a SHA-256 pre-hash keeps
# long passwords safe and makes verification deterministic.
_MAX_PASSWORD_BYTES = 72


class PasswordHashError(RuntimeError):
    """Raised when the configured password hash cannot be verified."""


def _prehash(password: str) -> bytes:
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return digest


def hash_password(password: str) -> str:
    """Hash a password with SHA-256 pre-hash + bcrypt (cost 12)."""
    if not password:
        raise ValueError("Password must not be empty")
    if len(password.encode("utf-8")) > 4096:
        raise ValueError("Password is too long")
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=12)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time-ish password verification. Returns False on any bad input."""
    if not password or not password_hash:
        return False
    try:
        candidate = _prehash(password[:_MAX_PASSWORD_BYTES * 4])
        stored = password_hash.encode("utf-8")
        return bcrypt.checkpw(candidate, stored)
    except (ValueError, TypeError):
        logger.warning("Password verification failed due to malformed stored hash")
        return False


class SessionManager:
    """Stateless, signed session tokens stored in an HttpOnly cookie.

    Serverless platforms have no shared durable memory between invocations, so
    sessions are cryptographically signed (itsdangerous) instead of stored.
    Mitigations: short TTL, HttpOnly + Secure + SameSite cookies, constant-time
    signature checks, and a password fingerprint embedded in the token so that
    **changing the admin password invalidates every existing session**.
    Limitation: a stolen cookie stays valid until its TTL, a logout, or a
    password/SESSION_SECRET rotation (logout only clears the browser cookie).
    """

    def __init__(
        self,
        secret: str | None,
        ttl_hours: int = 8,
        *,
        password_fingerprint: str = "",
    ) -> None:
        self._ttl_seconds = max(1, ttl_hours) * 3600
        self._configured = bool(secret)
        self._fingerprint = password_fingerprint[:16]
        self._serializer = URLSafeTimedSerializer(
            secret or "insecure-development-secret-change-me",
            salt=SESSION_SALT,
        )

    @property
    def configured(self) -> bool:
        return self._configured

    def create_token(self) -> str:
        payload = {
            "sid": secrets.token_urlsafe(18),
            "v": SESSION_VERSION,
            "pf": self._fingerprint,
        }
        return self._serializer.dumps(payload)

    def validate_token(self, token: str | None) -> dict | None:
        """Return the session payload, or None if invalid/expired/tampered."""
        if not token:
            return None
        try:
            payload = self._serializer.loads(token, max_age=self._ttl_seconds)
        except SignatureExpired:
            return None
        except BadSignature:
            return None
        if not isinstance(payload, dict) or payload.get("v") != SESSION_VERSION:
            return None
        if not isinstance(payload.get("sid"), str):
            return None
        if payload.get("pf", "") != self._fingerprint:
            # Password was rotated since this session was issued.
            return None
        return payload

    def ttl_seconds(self) -> int:
        return self._ttl_seconds


@dataclass
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int
    remaining: int


class SlidingWindowRateLimiter:
    """In-memory sliding-window limiter.

    On serverless this is *per warm instance* (best effort). It stops the most
    common brute-force bursts; for a globally strict limit, back this with a
    shared store. Keys are bounded and pruned to avoid unbounded memory.
    """

    def __init__(self, max_keys: int = 5000) -> None:
        self._lock = threading.Lock()
        self._events: dict[str, deque[float]] = {}
        self._max_keys = max_keys

    def check(self, key: str, limit: int, window_seconds: int) -> RateLimitResult:
        now = time.monotonic()
        cutoff = now - window_seconds
        with self._lock:
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= self._max_keys:
                    self._prune_locked(now, window_seconds)
                events = deque()
                self._events[key] = events
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(events[0] + window_seconds - now) + 1)
                return RateLimitResult(False, retry_after, 0)
            return RateLimitResult(True, 0, limit - len(events))

    def record(self, key: str) -> None:
        with self._lock:
            self._events.setdefault(key, deque()).append(time.monotonic())

    def clear(self, key: str) -> None:
        """Reset a key (e.g. after a successful login) so real users are not locked out."""
        with self._lock:
            self._events.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()

    def _prune_locked(self, now: float, window_seconds: int) -> None:
        cutoff = now - window_seconds
        stale = [k for k, v in self._events.items() if not v or v[-1] <= cutoff]
        for key in stale:
            self._events.pop(key, None)
        if len(self._events) >= self._max_keys:
            # Hard cap: drop oldest-inserted half of the keys.
            for key in list(self._events)[: self._max_keys // 2]:
                self._events.pop(key, None)


def origin_is_allowed(origin: str | None, host: str, extra_allowed: tuple[str, ...]) -> bool:
    """SameSite + Origin check for state-changing requests.

    Requests without an Origin header (same-origin navigations, curl, native
    clients) are allowed; browsers always send Origin on cross-site POSTs.
    """
    if not origin:
        return True
    if origin in extra_allowed:
        return True
    try:
        return hmac.compare_digest(origin.strip().rstrip("/"), host.strip().rstrip("/"))
    except (ValueError, AttributeError):
        return False
