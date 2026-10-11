"""Visitor persistence and aggregation on top of the JSONBin client."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.analytics import parse_iso_timestamp, utc_now_iso
from app.services.jsonbin import JSONBinClient, MAX_RECORDS

logger = logging.getLogger("view-info.visitor_store")


class VisitorStore:
    """CRUD + analytics over the visitor list stored in one JSONBin document.

    All writes are read-modify-write; see app.services.jsonbin for the
    concurrency and rate-limit limitations of JSONBin.io.
    """

    def __init__(self, client: JSONBinClient, *, retention_days: int = 90) -> None:
        self._client = client
        self._retention_days = max(1, retention_days)

    # -- internals ---------------------------------------------------------
    def _prune(self, visitors: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        kept: list[dict[str, Any]] = []
        dropped = 0
        for visitor in visitors:
            ts = parse_iso_timestamp(visitor.get("ts"))
            if ts is not None and ts < cutoff:
                dropped += 1
                continue
            kept.append(visitor)
        if len(kept) > MAX_RECORDS:
            dropped += len(kept) - MAX_RECORDS
            kept = kept[-MAX_RECORDS:]
        return kept, dropped

    async def _load(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        record = await self._client.get_record()
        visitors, pruned = self._prune(list(record.get("visitors", [])))
        return visitors, {"record": record, "pruned": pruned}

    async def _save(self, visitors: list[dict[str, Any]]) -> None:
        await self._client.put_record(
            {
                "schema_version": 1,
                "updated_at": utc_now_iso(),
                "visitors": visitors,
            }
        )

    # -- writes ------------------------------------------------------------
    async def record_visit(self, visit: dict[str, Any]) -> dict[str, Any]:
        """Append a visit, trimming expired records in the same write."""
        visitors, meta = await self._load()
        visitors.append(visit)
        await self._save(visitors)
        return {"visit_id": visit.get("id"), "pruned": meta["pruned"]}

    async def attach_precise_geo(self, visit_id: str, latitude: float, longitude: float) -> bool:
        """Attach opt-in precise coordinates to a visit the caller can prove."""
        visitors, meta = await self._load()
        updated = False
        for visitor in visitors:
            if visitor.get("id") == visit_id:
                visitor["lat"] = latitude
                visitor["lon"] = longitude
                visitor["geo_opt_in"] = True
                updated = True
                break
        if updated or meta["pruned"]:
            await self._save(visitors)
        return updated

    async def delete_visit(self, visit_id: str) -> bool:
        """Delete a visit by its unguessable id (admin action or visitor opt-out)."""
        visitors, meta = await self._load()
        remaining = [v for v in visitors if v.get("id") != visit_id]
        deleted = len(remaining) != len(visitors)
        if deleted or meta["pruned"]:
            await self._save(remaining)
        return deleted

    async def purge_older_than(self, days: int) -> int:
        """Admin-triggered cleanup; returns the number of removed records."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        visitors, meta = await self._load()
        kept: list[dict[str, Any]] = []
        removed = 0
        for visitor in visitors:
            ts = parse_iso_timestamp(visitor.get("ts"))
            if ts is not None and ts < cutoff:
                removed += 1
                continue
            kept.append(visitor)
        if removed or meta["pruned"]:
            await self._save(kept)
        return removed

    # -- reads -------------------------------------------------------------
    async def list_visitors(
        self,
        *,
        page: int = 1,
        page_size: int = 20,
        device: str | None = None,
        country: str | None = None,
    ) -> dict[str, Any]:
        visitors, _ = await self._load()
        if device:
            visitors = [v for v in visitors if (v.get("device") or "") == device]
        if country:
            visitors = [v for v in visitors if (v.get("country") or "").upper() == country.upper()]
        visitors = sorted(visitors, key=lambda v: v.get("ts") or "", reverse=True)
        total = len(visitors)
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        start = (page - 1) * page_size
        items = visitors[start : start + page_size]
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    async def stats(self) -> dict[str, Any]:
        visitors, meta = await self._load()
        total = len(visitors)
        devices = Counter((v.get("device") or "unknown") for v in visitors)
        browsers = Counter((v.get("browser") or "Other") for v in visitors)
        systems = Counter((v.get("os") or "Other") for v in visitors)
        countries = Counter((v.get("country") or "Unknown") for v in visitors)
        cities = Counter(v.get("city") for v in visitors if v.get("city"))
        recent = sorted(visitors, key=lambda v: v.get("ts") or "", reverse=True)[:5]

        now = datetime.now(timezone.utc)
        today = now.date()
        seven_days_ago = now - timedelta(days=7)
        visits_today = 0
        visits_7d = 0
        for visitor in visitors:
            ts = parse_iso_timestamp(visitor.get("ts"))
            if ts is None:
                continue
            if ts.date() == today:
                visits_today += 1
            if ts >= seven_days_ago:
                visits_7d += 1

        return {
            "total_visits": total,
            "visits_today": visits_today,
            "visits_7d": visits_7d,
            "devices": dict(devices.most_common()),
            "browsers": dict(browsers.most_common(8)),
            "operating_systems": dict(systems.most_common(8)),
            "countries": dict(countries.most_common(12)),
            "cities": dict(cities.most_common(12)),
            "recent": recent,
            "updated_at": meta["record"].get("updated_at"),
            "retention_days": self._retention_days,
        }
