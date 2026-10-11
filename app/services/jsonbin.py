"""JSONBin.io service module.

Storage layout (single bin document):

    {
      "schema_version": 1,
      "updated_at": "2026-01-01T00:00:00Z",
      "visitors": [ { ...visit record... }, ... ]
    }

Important JSONBin limitations (documented on purpose):

* Concurrent writes: JSONBin has **no transactions, locks, or compare-and-set**.
  Updates are read-modify-write of the whole document, so two concurrent
  serverless invocations can race and the last writer wins (a lost update).
  This app minimises the window (short read + write, single small document)
  but cannot eliminate it on a free JSON storage provider. If visit volume
  grows, move to a real datastore (Postgres, Upstash Redis, Vercel KV, etc.).
* Rate limits: JSONBin applies per-account request quotas (plan dependent) and
  may return 429. All calls here use timeouts, bounded retries on transient
  failures, and map errors to safe, secret-free API responses.
* Payload size: the whole document is sent on every write. The retention
  window (VISITOR_RETENTION_DAYS) and record cap keep the document small.

Credentials (JSONBIN_BIN_ID, JSONBIN_MASTER_KEY, JSONBIN_ACCESS_KEY) are read
server-side only and are never returned in responses or logs.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

logger = logging.getLogger("view-info.jsonbin")

JSONBIN_API_BASE = "https://api.jsonbin.io/v3"
SCHEMA_VERSION = 1
MAX_RECORDS = 5000
_TRANSIENT_STATUS = {429, 500, 502, 503, 504}


class JSONBinError(Exception):
    """Base class for JSONBin failures. Messages are safe to log (no secrets)."""

    code = "jsonbin_error"
    http_status = 502

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class JSONBinConfigError(JSONBinError):
    """Missing or incomplete configuration."""

    code = "jsonbin_not_configured"
    http_status = 503


class JSONBinUnavailableError(JSONBinError):
    """Network failure, timeout, or upstream 5xx."""

    code = "jsonbin_unavailable"
    http_status = 503


class JSONBinResponseError(JSONBinError):
    """JSONBin answered with an unexpected status."""

    code = "jsonbin_upstream_error"
    http_status = 502


def empty_record() -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "updated_at": None, "visitors": []}


class JSONBinClient:
    """Small, dependency-light async client for JSONBin.io v3 Bins API."""

    def __init__(
        self,
        bin_id: str | None,
        master_key: str | None,
        access_key: str | None = None,
        *,
        timeout_seconds: float = 10,
        base_url: str = JSONBIN_API_BASE,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = 2,
    ) -> None:
        self._bin_id = bin_id
        self._master_key = master_key
        self._access_key = access_key
        self._timeout = timeout_seconds
        self._base_url = base_url.rstrip("/")
        self._client = http_client
        self._owns_client = http_client is None
        self._max_retries = max(0, max_retries)

    # -- lifecycle ---------------------------------------------------------
    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        return self._client

    # -- config ------------------------------------------------------------
    def ensure_configured(self) -> None:
        if not self._bin_id or not self._master_key:
            missing = [
                name
                for name, value in (
                    ("JSONBIN_BIN_ID", self._bin_id),
                    ("JSONBIN_MASTER_KEY", self._master_key),
                )
                if not value
            ]
            raise JSONBinConfigError(
                "Visitor storage is not configured. Set "
                + ", ".join(missing)
                + " in the deployment environment variables (see .env.example / README)."
            )

    def _headers(self) -> dict[str, str]:
        headers = {
            "X-Master-Key": self._master_key or "",
            "Accept": "application/json",
        }
        if self._access_key:
            headers["X-Access-Key"] = self._access_key
        return headers

    # -- HTTP with bounded retries ----------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        *,
        content: bytes | None = None,
        params: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        self.ensure_configured()
        url = f"{self._base_url}{path}"
        headers = self._headers()
        if extra_headers:
            headers.update(extra_headers)
        if content is not None:
            headers.setdefault("Content-Type", "application/json")
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                response = await self._http().request(
                    method,
                    url,
                    content=content,
                    params=params,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning("JSONBin timeout on %s %s (attempt %s)", method, path, attempt + 1)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("JSONBin network error on %s %s (attempt %s)", method, path, attempt + 1)
            else:
                if response.status_code in _TRANSIENT_STATUS and attempt < self._max_retries:
                    logger.warning(
                        "JSONBin transient status %s on %s %s (attempt %s)",
                        response.status_code,
                        method,
                        path,
                        attempt + 1,
                    )
                else:
                    return response
            await asyncio.sleep(min(1.5 * (2**attempt), 4))
        raise JSONBinUnavailableError(
            f"Could not reach the visitor storage service ({method} {path}): {type(last_error).__name__}."
        )

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, context: str) -> Any:
        if response.status_code >= 400:
            # Never echo the response body verbatim (could contain account info).
            if response.status_code in _TRANSIENT_STATUS and response.status_code != 429:
                raise JSONBinUnavailableError(
                    f"Visitor storage is temporarily unavailable ({context}, HTTP {response.status_code})."
                )
            if response.status_code == 401 or response.status_code == 403:
                raise JSONBinResponseError(
                    f"Visitor storage rejected the credentials ({context}, HTTP {response.status_code}). "
                    "Check JSONBIN_MASTER_KEY / JSONBIN_ACCESS_KEY."
                )
            if response.status_code == 404:
                raise JSONBinResponseError(
                    f"Visitor storage bin was not found ({context}, HTTP 404). Check JSONBIN_BIN_ID."
                )
            if response.status_code == 429:
                raise JSONBinUnavailableError(
                    "Visitor storage rate limit reached (HTTP 429). Try again shortly."
                )
            raise JSONBinResponseError(
                f"Visitor storage returned HTTP {response.status_code} ({context})."
            )
        try:
            return response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise JSONBinResponseError(
                f"Visitor storage returned a non-JSON response ({context})."
            ) from exc

    # -- public API --------------------------------------------------------
    async def get_record(self) -> dict[str, Any]:
        """Read the latest bin document; returns an empty record when missing."""
        response = await self._request(
            "GET",
            f"/b/{self._bin_id}/latest",
            params={"meta": "false"},
        )
        if response.status_code == 404:
            return empty_record()
        payload = self._raise_for_status(response, context="read")
        record = payload.get("record", payload) if isinstance(payload, dict) else None
        return self._normalize(record)

    async def put_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Replace the whole bin document (read-modify-write; see module docs)."""
        body = json.dumps(record, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        response = await self._request(
            "PUT",
            f"/b/{self._bin_id}",
            content=body,
        )
        # Some plans answer 200, others 201.
        if response.status_code not in (200, 201):
            self._raise_for_status(response, context="write")
        try:
            payload = response.json()
        except ValueError:
            return record
        if isinstance(payload, dict) and "record" in payload:
            return self._normalize(payload.get("record"))
        return record

    @staticmethod
    def _normalize(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            return empty_record()
        visitors = raw.get("visitors")
        if not isinstance(visitors, list):
            visitors = []
        visitors = [v for v in visitors if isinstance(v, dict)]
        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": raw.get("updated_at"),
            "visitors": visitors,
        }
