"""Unit tests for the JSONBin service module (mocked transport, no network)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services.jsonbin import (
    JSONBinClient,
    JSONBinConfigError,
    JSONBinResponseError,
    JSONBinUnavailableError,
    empty_record,
)

BIN_ID = "bin_abc"
MASTER_KEY = "super-secret-master-key-do-not-leak"


def _run(coro):
    return asyncio.run(coro)


def _client(handler, **kwargs) -> JSONBinClient:
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    return JSONBinClient(
        BIN_ID,
        MASTER_KEY,
        kwargs.pop("access_key", None),
        http_client=http,
        max_retries=kwargs.pop("max_retries", 1),
        **kwargs,
    )


def test_missing_credentials_raise_config_error_listing_env_vars() -> None:
    client = JSONBinClient(None, None)
    with pytest.raises(JSONBinConfigError) as exc:
        client.ensure_configured()
    assert "JSONBIN_BIN_ID" in str(exc.value)
    assert "JSONBIN_MASTER_KEY" in str(exc.value)
    assert exc.value.code == "jsonbin_not_configured"
    assert exc.value.http_status == 503


def test_get_record_sends_master_key_and_parses_record() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["master"] = request.headers.get("X-Master-Key", "")
        seen["access"] = request.headers.get("X-Access-Key", "")
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={"record": {"schema_version": 1, "updated_at": "2026-01-01T00:00:00Z", "visitors": [{"id": "v1"}]}},
        )

    client = _client(handler, access_key="scoped-key")
    record = _run(client.get_record())
    assert record["visitors"] == [{"id": "v1"}]
    assert seen["master"] == MASTER_KEY
    assert seen["access"] == "scoped-key"
    assert "/latest" in seen["url"]


def test_get_record_normalizes_malformed_payloads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"record": {"visitors": "not-a-list", "junk": "value"}})

    record = _run(_client(handler).get_record())
    assert record == empty_record() | {"visitors": []}


def test_get_record_404_returns_empty_record() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Bin not found"})

    record = _run(_client(handler).get_record())
    assert record["visitors"] == []


def test_put_record_replaces_document() -> None:
    stored: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Content-Type"] == "application/json"
        stored.update(__import__("json").loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"record": stored})

    payload = {"schema_version": 1, "updated_at": "2026-01-02T00:00:00Z", "visitors": [{"id": "a"}]}
    result = _run(_client(handler).put_record(payload))
    assert result["visitors"] == [{"id": "a"}]
    assert stored["visitors"] == [{"id": "a"}]


def test_unauthorized_error_never_leaks_the_master_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Invalid Master Key " + MASTER_KEY})

    with pytest.raises(JSONBinResponseError) as exc:
        _run(_client(handler).get_record())
    assert MASTER_KEY not in str(exc.value)
    assert "credential" in str(exc.value).lower()


def test_forbidden_error_is_actionable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "nope"})

    with pytest.raises(JSONBinResponseError, match="JSONBIN_MASTER_KEY"):
        _run(_client(handler).get_record())


def test_bin_not_found_message_mentions_bin_id_var() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Bin not found"})

    # GET /latest 404 is treated as "empty bin"; PUT 404 is a real config error.
    with pytest.raises(JSONBinResponseError, match="JSONBIN_BIN_ID"):
        _run(_client(handler).put_record({"visitors": []}))


def test_rate_limited_response_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"message": "rate limited"})

    with pytest.raises(JSONBinUnavailableError, match="429"):
        _run(_client(handler, max_retries=0).get_record())


def test_transient_errors_are_retried_then_raise() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"message": "down"})

    with pytest.raises(JSONBinUnavailableError):
        _run(_client(handler, max_retries=2).get_record())
    assert calls["n"] == 3  # initial + 2 retries


def test_network_error_maps_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(JSONBinUnavailableError):
        _run(_client(handler, max_retries=0).get_record())


def test_non_json_response_is_a_safe_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>oops</html>")

    with pytest.raises(JSONBinResponseError, match="non-JSON"):
        _run(_client(handler).get_record())
