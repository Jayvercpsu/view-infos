"""Translate domain errors into safe, secret-free HTTP responses."""

from __future__ import annotations

from fastapi import HTTPException

from app.services.jsonbin import JSONBinError


def storage_http_exception(exc: JSONBinError) -> HTTPException:
    """Map a JSONBinError to an HTTPException with an actionable message.

    Never includes credentials, request bodies, or upstream response bodies.
    """
    return HTTPException(
        status_code=exc.http_status,
        detail={"code": exc.code, "message": exc.message},
    )
