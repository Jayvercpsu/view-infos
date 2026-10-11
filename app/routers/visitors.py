"""Visitor tracking (public, notice-based) and admin analytics endpoints."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from app import deps
from app.config import Settings, get_settings
from app.deps import (
    client_ip,
    enforce_origin,
    get_rate_limiter,
    get_visitor_store,
    require_admin,
)
from app.errors import storage_http_exception
from app.security import SlidingWindowRateLimiter
from app.services.analytics import build_visit_record, sanitize_precise_geo
from app.services.jsonbin import JSONBinError
from app.services.visitor_store import VisitorStore

logger = logging.getLogger("view-info.visitors")

router = APIRouter(prefix="/api", tags=["visitors"])


class GeoOptInRequest(BaseModel):
    visit_id: str = Field(min_length=8, max_length=64)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class DeleteVisitRequest(BaseModel):
    visit_id: str = Field(min_length=8, max_length=64)


async def _call(store_call):
    try:
        return await store_call
    except JSONBinError as exc:
        raise storage_http_exception(exc) from exc


@router.post("/track", status_code=status.HTTP_201_CREATED)
async def track_visit(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[VisitorStore, Depends(get_visitor_store)],
    limiter: Annotated[SlidingWindowRateLimiter, Depends(lambda: get_rate_limiter("track"))],
    _: Annotated[None, Depends(enforce_origin)],
) -> dict[str, Any]:
    """Record a visit. Requires the on-page privacy notice (analytics are
    disclosed and the visit can be deleted by its unguessable id)."""
    ip_key = f"track:{client_ip(request)}"
    verdict = limiter.check(
        ip_key,
        settings.track_rate_limit_attempts,
        settings.track_rate_limit_window_seconds,
    )
    if not verdict.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests.",
            headers={"Retry-After": str(verdict.retry_after_seconds)},
        )
    limiter.record(ip_key)

    record = build_visit_record(
        request,
        trust_proxy_headers=deps.TRUST_PROXY_HEADERS,
        anonymize=settings.anonymize_ip,
    )
    try:
        result = await store.record_visit(record)
    except JSONBinError as exc:
        raise storage_http_exception(exc) from exc
    return {
        "ok": True,
        "visit_id": result.get("visit_id"),
        "collected": [
            "timestamp",
            "ip_address",
            "approximate_location",
            "browser",
            "operating_system",
            "device_category",
            "referrer",
        ],
        "opt_out": "POST /api/track/delete with this visit_id removes the record.",
    }


@router.post("/track/geo")
async def track_precise_geo(
    payload: GeoOptInRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    store: Annotated[VisitorStore, Depends(get_visitor_store)],
    _: Annotated[None, Depends(enforce_origin)],
) -> dict[str, Any]:
    """Attach precise GPS coordinates, only after the browser permission prompt."""
    sanitized = sanitize_precise_geo(payload.latitude, payload.longitude)
    if sanitized is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid coordinates.",
        )
    lat, lon = sanitized
    try:
        updated = await store.attach_precise_geo(payload.visit_id, lat, lon)
    except JSONBinError as exc:
        raise storage_http_exception(exc) from exc
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Visit not found.",
        )
    return {"ok": True, "latitude": lat, "longitude": lon}


@router.post("/track/delete")
async def track_delete(
    payload: DeleteVisitRequest,
    store: Annotated[VisitorStore, Depends(get_visitor_store)],
    _: Annotated[None, Depends(enforce_origin)],
) -> dict[str, Any]:
    """Opt out: delete a visit using its unguessable id (returned by /api/track)."""
    try:
        deleted = await store.delete_visit(payload.visit_id)
    except JSONBinError as exc:
        raise storage_http_exception(exc) from exc
    return {"ok": True, "deleted": deleted}


# --- admin-only analytics ---------------------------------------------------

@router.get("/admin/stats")
async def admin_stats(
    _: Annotated[dict[str, Any], Depends(require_admin)],
    store: Annotated[VisitorStore, Depends(get_visitor_store)],
) -> dict[str, Any]:
    try:
        return await store.stats()
    except JSONBinError as exc:
        raise storage_http_exception(exc) from exc


@router.get("/admin/visitors")
async def admin_list_visitors(
    _: Annotated[dict[str, Any], Depends(require_admin)],
    store: Annotated[VisitorStore, Depends(get_visitor_store)],
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    device: Annotated[str | None, Query(max_length=20)] = None,
    country: Annotated[str | None, Query(max_length=8)] = None,
) -> dict[str, Any]:
    try:
        return await store.list_visitors(
            page=page,
            page_size=page_size,
            device=device,
            country=country,
        )
    except JSONBinError as exc:
        raise storage_http_exception(exc) from exc


@router.delete("/admin/visitors/{visit_id}")
async def admin_delete_visitor(
    visit_id: str,
    _: Annotated[dict[str, Any], Depends(require_admin)],
    store: Annotated[VisitorStore, Depends(get_visitor_store)],
) -> dict[str, Any]:
    if len(visit_id) > 64:
        raise HTTPException(status_code=422, detail="Invalid visit id.")
    try:
        deleted = await store.delete_visit(visit_id)
    except JSONBinError as exc:
        raise storage_http_exception(exc) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Visit not found.")
    return {"ok": True}


@router.post("/admin/visitors/purge")
async def admin_purge(
    _: Annotated[dict[str, Any], Depends(require_admin)],
    store: Annotated[VisitorStore, Depends(get_visitor_store)],
    days: Annotated[int, Query(ge=1, le=3650)] = 90,
) -> dict[str, Any]:
    try:
        removed = await store.purge_older_than(days)
    except JSONBinError as exc:
        raise storage_http_exception(exc) from exc
    return {"ok": True, "removed": removed}
