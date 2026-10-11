"""Authorization tests: every admin API must be protected."""

from __future__ import annotations

from typing import Any

import pytest

ADMIN_ENDPOINTS = [
    ("GET", "/api/admin/stats"),
    ("GET", "/api/admin/visitors"),
    ("GET", "/api/admin/visitors?page=2&page_size=5"),
    ("GET", "/api/admin/visitors?device=mobile"),
    ("DELETE", "/api/admin/visitors/some-visit-id"),
    ("POST", "/api/admin/visitors/purge?days=30"),
]


@pytest.mark.parametrize("method,path", ADMIN_ENDPOINTS)
def test_admin_endpoints_require_auth(client: Any, method: str, path: str) -> None:
    response = client.request(method, path)
    assert response.status_code == 401, f"{method} {path} -> {response.status_code}"
    assert "Invalid password" not in response.text


@pytest.mark.parametrize("method,path", ADMIN_ENDPOINTS)
def test_admin_endpoints_reject_garbage_tokens(admin_client: Any, method: str, path: str) -> None:
    admin_client.cookies.clear()
    admin_client.cookies.set("surprise_session", "garbage.token.value")
    response = admin_client.request(method, path)
    assert response.status_code == 401


def test_admin_endpoints_work_with_valid_session(admin_client: Any) -> None:
    stats = admin_client.get("/api/admin/stats")
    assert stats.status_code == 200
    assert stats.json()["total_visits"] == 0

    listing = admin_client.get("/api/admin/visitors")
    assert listing.status_code == 200
    assert listing.json()["items"] == []


def test_admin_stats_never_leak_storage_credentials(admin_client: Any) -> None:
    body = admin_client.get("/api/admin/stats").text
    assert "test-master-key" not in body
    assert "JSONBIN_MASTER_KEY" not in body
    assert "master_key" not in body.lower()


def test_logout_invalidates_access_immediately(client: Any) -> None:
    client.post("/api/auth/login", json={"password": "admin111322?"})
    assert client.get("/api/admin/stats").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/admin/stats").status_code == 401


def test_public_endpoints_stay_public(client: Any, trusted_headers: dict[str, str]) -> None:
    assert client.get("/").status_code == 200
    assert client.get("/admin-private").status_code == 200
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/track", json={}, headers=trusted_headers).status_code == 201
