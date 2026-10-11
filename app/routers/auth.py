"""Admin authentication endpoints (password-only, session cookie based)."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.deps import (
    client_ip,
    cookie_secure_flag,
    enforce_origin,
    get_admin_optional,
    get_rate_limiter,
    get_session_manager,
)
from app.security import (
    SESSION_COOKIE_NAME,
    SessionManager,
    SlidingWindowRateLimiter,
    verify_password,
)

logger = logging.getLogger("view-info.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

GENERIC_LOGIN_ERROR = "Invalid password."


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


@lru_cache(maxsize=4)
def _resolve_stored_hash_cached(admin_password_hash: str, admin_password: str) -> str:
    """Return the bcrypt hash to verify against (hash env or hashed dev password)."""
    if admin_password_hash:
        return admin_password_hash
    if admin_password:
        import bcrypt
        import hashlib

        return bcrypt.hashpw(
            hashlib.sha256(admin_password.encode("utf-8")).digest(),
            bcrypt.gensalt(rounds=12),
        ).decode("utf-8")
    return ""


def _resolve_stored_hash(settings: Settings) -> str:
    return _resolve_stored_hash_cached(
        settings.admin_password_hash or "",
        settings.admin_password or "",
    )


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    session_manager: Annotated[SessionManager, Depends(get_session_manager)],
    limiter: Annotated[SlidingWindowRateLimiter, Depends(lambda: get_rate_limiter("login"))],
    _: Annotated[None, Depends(enforce_origin)],
) -> dict[str, Any]:
    ip = client_ip(request)
    key = f"login:{ip}"
    verdict = limiter.check(
        key,
        settings.login_rate_limit_attempts,
        settings.login_rate_limit_window_seconds,
    )
    if not verdict.allowed:
        logger.warning("Login rate limit exceeded")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Please try again later.",
            headers={"Retry-After": str(verdict.retry_after_seconds)},
        )

    limiter.record(key)

    if not settings.auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin login is not configured on this deployment.",
        )

    stored_hash = _resolve_stored_hash(settings)
    ok = verify_password(payload.password, stored_hash)
    if not ok:
        # Burn comparable CPU so success/failure timing is similar.
        verify_password("timing-equalizer-not-the-password", stored_hash)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_LOGIN_ERROR,
        )

    token = session_manager.create_token()
    secure = cookie_secure_flag(settings, request)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    limiter.clear(key)  # a real human just signed in: do not count against them
    return {
        "ok": True,
        "expires_in_seconds": session_manager.ttl_seconds(),
        "cookie_secure": secure,
    }


@router.post("/logout")
async def logout(
    response: Response,
    admin: Annotated[dict[str, Any] | None, Depends(get_admin_optional)],
) -> dict[str, Any]:
    # Works even when already logged out; always clears the cookie.
    response.delete_cookie(
        key=SESSION_COOKIE_NAME,
        path="/",
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}


@router.get("/session")
async def session_info(
    settings: Annotated[Settings, Depends(get_settings)],
    admin: Annotated[dict[str, Any] | None, Depends(get_admin_optional)],
) -> dict[str, Any]:
    return {
        "authenticated": admin is not None,
        "auth_configured": settings.auth_configured,
    }
