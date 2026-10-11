"""FastAPI application factory for the view-info surprise site."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.routers import auth, pages, visitors
from app.services.jsonbin import JSONBinError

logger = logging.getLogger("view-info")

PUBLIC_DIR = Path(__file__).resolve().parents[1] / "public"


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        for warning in settings.startup_warnings():
            logger.warning(warning)
        yield

    app = FastAPI(
        title="view-info — Surprise",
        version="1.0.0",
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    @app.exception_handler(JSONBinError)
    async def jsonbin_error_handler(_: Request, exc: JSONBinError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"detail": {"code": exc.code, "message": exc.message}},
        )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """Defence in depth: same hardening locally as vercel.json provides in prod."""
        response = await call_next(request)
        path = request.url.path
        headers = response.headers
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault(
            "Permissions-Policy",
            "geolocation=(self), camera=(), microphone=(), interest-cohort=()",
        )
        headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
            " https://fonts.googleapis.com; font-src https://fonts.gstatic.com;"
            " img-src 'self' data:; connect-src 'self'; base-uri 'none';"
            " form-action 'self'; frame-ancestors 'none'",
        )
        if request.url.scheme == "https":
            headers.setdefault(
                "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
            )
        if path.startswith("/admin-private") or path.startswith("/api/admin") or path.startswith("/api/auth"):
            headers.setdefault("X-Robots-Tag", "noindex, nofollow")
            # Auth/session data must never be cached by browsers or proxies.
            headers.setdefault("Cache-Control", "no-store")
            headers.setdefault("Pragma", "no-cache")
        return response

    @app.get("/api/health", tags=["meta"])
    async def health() -> dict:
        """Health/config check. Never returns secrets."""
        return {
            "status": "ok",
            "env": settings.env,
            "auth_configured": settings.auth_configured,
            "jsonbin_configured": settings.jsonbin_configured,
            "missing_jsonbin_vars": settings.missing_jsonbin_vars() if not settings.jsonbin_configured else [],
        }

    app.include_router(pages.router)
    app.include_router(auth.router)
    app.include_router(visitors.router)

    # Static assets (local dev). On Vercel, files in public/ are also served
    # from the CDN; the explicit page routes above win for / and /admin-private.
    if PUBLIC_DIR.is_dir():
        app.mount("/assets", StaticFiles(directory=str(PUBLIC_DIR / "assets")), name="assets")

    return app
