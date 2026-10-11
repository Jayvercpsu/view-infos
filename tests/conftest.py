"""Shared fixtures: isolated settings, an in-memory JSONBin double, and app clients."""

from __future__ import annotations

import json
import os
from typing import Any, Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from app import deps
from app.config import reset_settings_cache
from app.main import create_app
from app.security import hash_password
from app.services.jsonbin import JSONBinClient

TEST_PASSWORD = "admin111322?"
TEST_PASSWORD_HASH = hash_password(TEST_PASSWORD)
TEST_SESSION_SECRET = "test-session-secret-value-abcdefghijklmnopqrstuvwxyz"


class FakeJSONBin:
    """In-memory stand-in for the JSONBin v3 Bins API (single bin, no versions)."""

    def __init__(self, bin_id: str = "bin_test123", master_key: str = "test-master-key") -> None:
        self.bin_id = bin_id
        self.master_key = master_key
        self.access_key: str | None = None
        self.record: Any = {"schema_version": 1, "updated_at": None, "visitors": []}
        self.requests: list[httpx.Request] = []
        self.fail_with: int | None = None
        self.delay_seconds: float = 0.0

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host != "api.jsonbin.io":
            return httpx.Response(404, json={"message": "unknown host"})

        key = request.headers.get("X-Master-Key")
        if key != self.master_key:
            return httpx.Response(401, json={"message": "Invalid Master Key"})

        if self.fail_with is not None:
            return httpx.Response(self.fail_with, json={"message": "forced failure"})

        path = request.url.path
        if request.method == "GET" and path.endswith("/latest"):
            return httpx.Response(200, json={"record": self.record, "metadata": {"id": self.bin_id}})
        if request.method == "PUT" and path.rstrip("/").endswith(self.bin_id):
            self.record = json.loads(request.content.decode("utf-8"))
            return httpx.Response(200, json={"record": self.record, "metadata": {"id": self.bin_id}})
        return httpx.Response(404, json={"message": "not found"})

    def make_client(self, *, timeout: float = 5.0, max_retries: int = 1) -> JSONBinClient:
        transport = httpx.MockTransport(self.handler)
        http = httpx.AsyncClient(transport=transport)
        return JSONBinClient(
            self.bin_id,
            self.master_key,
            self.access_key,
            timeout_seconds=timeout,
            http_client=http,
            max_retries=max_retries,
        )


def _reset_app_state() -> None:
    reset_settings_cache()
    deps._client_instances.clear()
    deps._store_instances.clear()
    deps._session_managers.clear()
    deps.get_rate_limiter("login").reset()
    deps.get_rate_limiter("track").reset()


def configure_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fake_bin: FakeJSONBin | None = None,
    jsonbin: bool = True,
    extra: dict[str, str] | None = None,
) -> None:
    """Point the app at a test environment (call before create_app)."""
    _reset_app_state()
    monkeypatch.setenv("ENV", "development")
    monkeypatch.setenv("SESSION_SECRET", TEST_SESSION_SECRET)
    monkeypatch.setenv("ADMIN_PASSWORD_HASH", TEST_PASSWORD_HASH)
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_ATTEMPTS", "4")
    monkeypatch.setenv("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "120")
    monkeypatch.setenv("TRACK_RATE_LIMIT_ATTEMPTS", "50")
    if jsonbin and fake_bin is not None:
        monkeypatch.setenv("JSONBIN_BIN_ID", fake_bin.bin_id)
        monkeypatch.setenv("JSONBIN_MASTER_KEY", fake_bin.master_key)
    else:
        monkeypatch.delenv("JSONBIN_BIN_ID", raising=False)
        monkeypatch.delenv("JSONBIN_MASTER_KEY", raising=False)
    for key, value in (extra or {}).items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    _reset_app_state()


@pytest.fixture
def fake_bin() -> FakeJSONBin:
    return FakeJSONBin()


@pytest.fixture
def app_factory(monkeypatch: pytest.MonkeyPatch, fake_bin: FakeJSONBin) -> Callable[[], Any]:
    """Factory producing a TestClient wired to the fake JSONBin backend."""

    def factory(**kwargs: Any) -> TestClient:
        configure_env(monkeypatch, fake_bin=fake_bin, **kwargs)
        app = create_app()
        client = fake_bin.make_client()
        app.dependency_overrides[deps.get_jsonbin_client] = lambda: client
        return TestClient(app)

    return factory


@pytest.fixture
def client(app_factory: Callable[[], Any]) -> Any:
    with app_factory() as test_client:
        yield test_client


@pytest.fixture
def admin_client(app_factory: Callable[[], Any]) -> Any:
    """A client that is already authenticated as admin."""
    with app_factory() as test_client:
        response = test_client.post("/api/auth/login", json={"password": TEST_PASSWORD})
        assert response.status_code == 200, response.text
        yield test_client


@pytest.fixture
def trusted_headers() -> dict[str, str]:
    return {
        "x-real-ip": "203.0.113.42",
        "x-vercel-ip-country": "PH",
        "x-vercel-ip-city": "Manila",
        "x-vercel-ip-country-region": "NCR",
        "user-agent": (
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
        ),
        "referer": "https://news.example.com/some-article",
    }


@pytest.fixture(autouse=True)
def _no_ambient_vercel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests hermetic: never trust Vercel headers unless a test asks."""
    monkeypatch.setenv("VERCEL", "")
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.setattr(deps, "TRUST_PROXY_HEADERS", False)
    os.environ.pop("VERCEL", None)
