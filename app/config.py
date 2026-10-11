"""Application configuration loaded from environment variables.

No secret is ever returned to the browser; this module is server-side only.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from functools import lru_cache

try:  # Optional: local development convenience; Vercel injects env directly.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is optional
    pass

logger = logging.getLogger("view-info.config")

_TRUTHY = {"1", "true", "yes", "on"}


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int | None = None) -> int:
    raw = _env_str(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid integer for %s; using default %s", name, default)
        return default
    if value < minimum:
        return minimum
    if maximum is not None and value > maximum:
        return maximum
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env_str(name)
    if raw is None:
        return default
    return raw.lower() in _TRUTHY


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable runtime configuration."""

    env: str = "development"
    admin_password_hash: str | None = None
    admin_password: str | None = None
    session_secret: str | None = None
    session_ttl_hours: int = 8
    cookie_secure: str = "auto"
    jsonbin_bin_id: str | None = None
    jsonbin_master_key: str | None = None
    jsonbin_access_key: str | None = None
    jsonbin_timeout_seconds: int = 10
    visitor_retention_days: int = 90
    anonymize_ip: bool = False
    login_rate_limit_attempts: int = 5
    login_rate_limit_window_seconds: int = 300
    track_rate_limit_attempts: int = 30
    track_rate_limit_window_seconds: int = 60
    extra_allowed_origins: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def jsonbin_configured(self) -> bool:
        return bool(self.jsonbin_bin_id and self.jsonbin_master_key)

    @property
    def auth_configured(self) -> bool:
        return bool(self.admin_password_hash or self.admin_password)

    def missing_jsonbin_vars(self) -> list[str]:
        missing: list[str] = []
        if not self.jsonbin_bin_id:
            missing.append("JSONBIN_BIN_ID")
        if not self.jsonbin_master_key:
            missing.append("JSONBIN_MASTER_KEY")
        return missing

    def startup_warnings(self) -> list[str]:
        """Actionable, secret-free startup warnings."""
        warnings: list[str] = []
        if not self.auth_configured:
            warnings.append(
                "ADMIN_PASSWORD_HASH (or ADMIN_PASSWORD for local dev) is not set; "
                "admin login is disabled until it is configured."
            )
        if self.is_production and not self.session_secret:
            warnings.append("SESSION_SECRET is not set; secure admin sessions are unavailable.")
        if self.is_production and self.session_secret and len(self.session_secret) < 32:
            warnings.append("SESSION_SECRET is shorter than 32 characters; use a stronger secret.")
        missing = self.missing_jsonbin_vars()
        if missing:
            warnings.append(
                "Visitor analytics are disabled: missing " + ", ".join(missing) + "."
            )
        return warnings


def _load_settings() -> Settings:
    env = (_env_str("ENV", "development") or "development").lower()
    extra_origins = tuple(
        origin.strip()
        for origin in (_env_str("EXTRA_ALLOWED_ORIGINS", "") or "").split(",")
        if origin.strip()
    )
    return Settings(
        env=env,
        admin_password_hash=_env_str("ADMIN_PASSWORD_HASH"),
        admin_password=_env_str("ADMIN_PASSWORD"),
        session_secret=_env_str("SESSION_SECRET"),
        session_ttl_hours=_env_int("SESSION_TTL_HOURS", 8, minimum=1, maximum=720),
        cookie_secure=(_env_str("COOKIE_SECURE", "auto") or "auto").lower(),
        jsonbin_bin_id=_env_str("JSONBIN_BIN_ID"),
        jsonbin_master_key=_env_str("JSONBIN_MASTER_KEY"),
        jsonbin_access_key=_env_str("JSONBIN_ACCESS_KEY"),
        jsonbin_timeout_seconds=_env_int("JSONBIN_TIMEOUT_SECONDS", 10, minimum=1, maximum=60),
        visitor_retention_days=_env_int("VISITOR_RETENTION_DAYS", 90, minimum=1, maximum=3650),
        anonymize_ip=_env_bool("ANONYMIZE_IP", False),
        login_rate_limit_attempts=_env_int("LOGIN_RATE_LIMIT_ATTEMPTS", 5, minimum=1),
        login_rate_limit_window_seconds=_env_int("LOGIN_RATE_LIMIT_WINDOW_SECONDS", 300, minimum=10),
        track_rate_limit_attempts=_env_int("TRACK_RATE_LIMIT_ATTEMPTS", 30, minimum=1),
        track_rate_limit_window_seconds=_env_int("TRACK_RATE_LIMIT_WINDOW_SECONDS", 60, minimum=10),
        extra_allowed_origins=extra_origins,
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return _load_settings()


def reset_settings_cache() -> None:
    """Testing helper: clear the cached settings so env changes take effect."""
    get_settings.cache_clear()
