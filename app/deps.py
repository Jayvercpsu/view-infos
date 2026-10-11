"""Server-side dependency wiring (settings, store, admin auth)."""

from __future__ import annotations

import hashlib
import os
from typing import Annotated, Any

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from app.config import Settings, get_settings
from app.security import (
    SESSION_COOKIE_NAME,
    SessionManager,
    SlidingWindowRateLimiter,
    origin_is_allowed,
)
from app.services.jsonbin import JSONBinClient
from app.services.visitor_store import VisitorStore

# Process-wide singletons (one per warm serverless instance).
_rate_limiters = {
    "login": SlidingWindowRateLimiter(),
    "track": SlidingWindowRateLimiter(),
}
_session_managers: dict[int, SessionManager] = {}
_store_instances: dict[tuple, VisitorStore] = {}
_client_instances: dict[tuple, JSONBinClient] = {}

TRUST_PROXY_HEADERS = os.environ.get("VERCEL") == "1" or os.environ.get("TRUST_VERCEL_HEADERS") == "1"


def get_rate_limiter(name: str) -> SlidingWindowRateLimiter:
    return _rate_limiters[name]


def get_session_manager(settings: Annotated[Settings, Depends(get_settings)]) -> SessionManager:
    key = id(settings)
    manager = _session_managers.get(key)
    if manager is None:
        # Fingerprint of the current password: rotating the admin password
        # invalidates every previously issued session cookie.
        secret_material = settings.admin_password_hash or settings.admin_password or ""
        fingerprint = hashlib.sha256(secret_material.encode("utf-8")).hexdigest()[:16]
        manager = SessionManager(
            settings.session_secret,
            settings.session_ttl_hours,
            password_fingerprint=fingerprint,
        )
        _session_managers[key] = manager
    return manager


def get_jsonbin_client(settings: Annotated[Settings, Depends(get_settings)]) -> JSONBinClient:
    key = (
        settings.jsonbin_bin_id,
        settings.jsonbin_master_key,
        settings.jsonbin_access_key,
        settings.jsonbin_timeout_seconds,
    )
    client = _client_instances.get(key)
    if client is None:
        client = JSONBinClient(
            settings.jsonbin_bin_id,
            settings.jsonbin_master_key,
            settings.jsonbin_access_key,
            timeout_seconds=settings.jsonbin_timeout_seconds,
        )
        _client_instances[key] = client
    return client


def get_visitor_store(
    settings: Annotated[Settings, Depends(get_settings)],
    client: Annotated[JSONBinClient, Depends(get_jsonbin_client)],
) -> VisitorStore:
    key = (
        settings.jsonbin_bin_id,
        settings.jsonbin_master_key,
        settings.jsonbin_access_key,
        settings.visitor_retention_days,
    )
    store = _store_instances.get(key)
    if store is None:
        store = VisitorStore(client, retention_days=settings.visitor_retention_days)
        _store_instances[key] = store
    return store


def client_ip(request: Request) -> str:
    """Best-effort client IP for rate limiting (never echoed back)."""
    if TRUST_PROXY_HEADERS:
        candidate = request.headers.get("x-real-ip")
        if candidate:
            return candidate.strip()[:64]
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def enforce_origin(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Reject cross-site state-changing requests (CSRF defence in depth)."""
    host = request.headers.get("host", "")
    origin = request.headers.get("origin")
    if not origin_is_allowed(origin, f"http://{host}" if host else "", settings.extra_allowed_origins):
        # Also accept https://host when the request is TLS-terminated upstream.
        if not origin_is_allowed(
            origin,
            f"https://{host}" if host else "",
            settings.extra_allowed_origins,
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cross-origin request rejected.",
            )


def require_admin(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any]:
    """Protect admin endpoints: HttpOnly signed cookie (or Bearer for scripts)."""
    payload = _validated_session(session_manager, session_cookie, authorization)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Admin authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not settings.auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin authentication is not configured on this deployment.",
        )
    request.state.session = payload
    return payload


def get_admin_optional(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
    authorization: Annotated[str | None, Header()] = None,
) -> dict[str, Any] | None:
    """Like require_admin, but returns None instead of raising."""
    payload = _validated_session(session_manager, session_cookie, authorization)
    if payload is None or not settings.auth_configured:
        return None
    request.state.session = payload
    return payload


def _validated_session(
    session_manager: SessionManager,
    session_cookie: str | None,
    authorization: str | None,
) -> dict[str, Any] | None:
    token = session_cookie
    if not token and authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    return session_manager.validate_token(token)


def cookie_secure_flag(settings: Settings, request: Request) -> bool:
    """Secure cookie flag: auto (HTTPS or Vercel), always, or never."""
    mode = settings.cookie_secure
    if mode == "true":
        return True
    if mode == "false":
        return False
    return request.url.scheme == "https" or bool(os.environ.get("VERCEL"))
