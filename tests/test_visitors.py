"""Visitor tracking + admin analytics integration tests (fake JSONBin)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app import deps


def _login(client: Any) -> None:
    assert client.post("/api/auth/login", json={"password": "admin111322?"}).status_code == 200


def test_track_records_visit_with_trusted_headers(
    client: Any, admin_client: Any, trusted_headers: dict[str, str], monkeypatch: Any
) -> None:
    monkeypatch.setattr(deps, "TRUST_PROXY_HEADERS", True)
    response = client.post("/api/track", json={}, headers=trusted_headers)
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["ok"] is True
    visit_id = payload["visit_id"]
    assert visit_id
    assert "timestamp" in payload["collected"]

    listing = admin_client.get("/api/admin/visitors").json()
    assert listing["total"] == 1
    item = listing["items"][0]
    assert item["id"] == visit_id
    assert item["ip"] == "203.0.113.42"
    assert item["country"] == "PH"
    assert item["city"] == "Manila"
    assert item["device"] == "mobile"
    assert item["browser"] == "Chrome"
    assert item["os"] == "Android"
    assert item["referrer"] == "https://news.example.com/some-article"
    assert item["ts"].endswith("Z")
    assert item["lat"] is None and item["geo_opt_in"] is False


def test_track_without_trusted_headers_falls_back_to_socket_ip(client: Any) -> None:
    response = client.post("/api/track", json={}, headers={"x-real-ip": "8.8.8.8"})
    assert response.status_code == 201
    # x-real-ip must be ignored when not behind the trusted proxy.
    _login(client)
    item = client.get("/api/admin/visitors").json()["items"][0]
    assert item["ip"] != "8.8.8.8"
    assert item["country"] is None


def test_track_anonymizes_ip_when_configured(
    app_factory: Callable[[], Any], trusted_headers: dict[str, str], monkeypatch: Any
) -> None:
    with app_factory(extra={"ANONYMIZE_IP": "true"}) as client:
        monkeypatch.setattr(deps, "TRUST_PROXY_HEADERS", True)
        assert client.post("/api/track", json={}, headers=trusted_headers).status_code == 201
        _login(client)
        item = client.get("/api/admin/visitors").json()["items"][0]
        assert item["ip"] == "203.0.113.0/24"
        assert item["ip_anonymized"] is True


def test_geo_opt_in_rounds_and_attaches(client: Any, trusted_headers: dict[str, str], monkeypatch: Any) -> None:
    monkeypatch.setattr(deps, "TRUST_PROXY_HEADERS", True)
    visit_id = client.post("/api/track", json={}, headers=trusted_headers).json()["visit_id"]

    response = client.post(
        "/api/track/geo",
        json={"visit_id": visit_id, "latitude": 14.5995123, "longitude": 120.9842229},
    )
    assert response.status_code == 200, response.text
    assert response.json()["latitude"] == 14.6
    assert response.json()["longitude"] == 120.984

    _login(client)
    item = client.get("/api/admin/visitors").json()["items"][0]
    assert item["geo_opt_in"] is True
    assert item["lat"] == 14.6


def test_geo_opt_in_rejects_invalid_coordinates(client: Any, trusted_headers: dict[str, str]) -> None:
    visit_id = client.post("/api/track", json={}, headers=trusted_headers).json()["visit_id"]
    bad = client.post(
        "/api/track/geo", json={"visit_id": visit_id, "latitude": 999, "longitude": 0}
    )
    assert bad.status_code == 422


def test_geo_opt_in_unknown_visit_is_404(client: Any) -> None:
    response = client.post(
        "/api/track/geo",
        json={"visit_id": "11111111-2222-3333-4444-555555555555", "latitude": 1, "longitude": 1},
    )
    assert response.status_code == 404


def test_visitor_can_delete_own_visit(client: Any, admin_client: Any, trusted_headers: dict[str, str]) -> None:
    visit_id = client.post("/api/track", json={}, headers=trusted_headers).json()["visit_id"]
    delete = client.post("/api/track/delete", json={"visit_id": visit_id})
    assert delete.status_code == 200
    assert delete.json()["deleted"] is True
    assert admin_client.get("/api/admin/visitors").json()["total"] == 0


def test_admin_can_delete_a_visit(admin_client: Any, client: Any, trusted_headers: dict[str, str]) -> None:
    visit_id = client.post("/api/track", json={}, headers=trusted_headers).json()["visit_id"]
    response = admin_client.delete(f"/api/admin/visitors/{visit_id}")
    assert response.status_code == 200
    assert admin_client.get("/api/admin/visitors").json()["total"] == 0
    assert admin_client.delete(f"/api/admin/visitors/{visit_id}").status_code == 404


def test_pagination_and_filters(admin_client: Any, client: Any) -> None:
    for i in range(7):
        headers = {
            "user-agent": (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
                "Version/17.0 Mobile/15E148 Safari/604.1"
                if i % 2 == 0
                else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Firefox/127.0"
            )
        }
        assert client.post("/api/track", json={}, headers=headers).status_code == 201

    page1 = admin_client.get("/api/admin/visitors?page=1&page_size=3").json()
    assert page1["total"] == 7
    assert page1["pages"] == 3
    assert len(page1["items"]) == 3

    page3 = admin_client.get("/api/admin/visitors?page=3&page_size=3").json()
    assert len(page3["items"]) == 1

    mobile = admin_client.get("/api/admin/visitors?device=mobile").json()
    assert mobile["total"] == 4
    assert all(item["device"] == "mobile" for item in mobile["items"])

    desktop = admin_client.get("/api/admin/visitors?device=desktop").json()
    assert desktop["total"] == 3


def test_stats_aggregation(admin_client: Any, client: Any) -> None:
    client.post("/api/track", json={}, headers={"user-agent": "Mozilla/5.0 (Windows NT 10.0) Chrome/126.0"})
    client.post("/api/track", json={}, headers={"user-agent": "Mozilla/5.0 (iPhone) Safari/605.1.15"})
    stats = admin_client.get("/api/admin/stats").json()
    assert stats["total_visits"] == 2
    assert stats["visits_today"] == 2
    assert stats["visits_7d"] == 2
    assert stats["devices"]["mobile"] == 1
    assert stats["devices"]["desktop"] == 1
    assert len(stats["recent"]) == 2
    assert stats["retention_days"] == 90


def test_retention_prunes_old_visits(
    app_factory: Callable[[], Any],
    fake_bin: Any,
    trusted_headers: dict[str, str],
    monkeypatch: Any,
) -> None:
    old_ts = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat().replace("+00:00", "Z")
    fresh_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    fake_bin.record["visitors"] = [
        {"id": "expired-1", "ts": old_ts, "device": "desktop", "browser": "x", "os": "x", "country": None},
        {"id": "fresh-1", "ts": fresh_ts, "device": "mobile", "browser": "y", "os": "y", "country": None},
    ]

    with app_factory() as client:
        monkeypatch.setattr(deps, "TRUST_PROXY_HEADERS", True)
        _login(client)

        # Expired records are invisible to the admin (pruned on read).
        listing = client.get("/api/admin/visitors").json()
        assert listing["total"] == 1
        assert listing["items"][0]["id"] == "fresh-1"

        # A subsequent write permanently drops the expired record.
        assert client.post("/api/track", json={}, headers=trusted_headers).status_code == 201
        stored_ids = {v["id"] for v in fake_bin.record["visitors"]}
        assert "expired-1" not in stored_ids
        assert "fresh-1" in stored_ids


def test_purge_endpoint(admin_client: Any, client: Any) -> None:
    client.post("/api/track", json={}, headers={"user-agent": "curl/8.0"})
    response = admin_client.post("/api/admin/visitors/purge?days=90")
    assert response.status_code == 200
    assert response.json()["removed"] == 0  # visit is fresh


def test_missing_jsonbin_credentials_return_safe_actionable_error(
    monkeypatch: Any, trusted_headers: dict[str, str]
) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import configure_env

    configure_env(monkeypatch, jsonbin=False)
    with TestClient(create_app()) as client:
        tracked = client.post("/api/track", json={}, headers=trusted_headers)
        assert tracked.status_code == 503
        detail = tracked.json()["detail"]
        assert detail["code"] == "jsonbin_not_configured"
        assert "JSONBIN_BIN_ID" in detail["message"]
        assert "JSONBIN_MASTER_KEY" in detail["message"]

        health = client.get("/api/health").json()
        assert health["jsonbin_configured"] is False
        assert set(health["missing_jsonbin_vars"]) == {"JSONBIN_BIN_ID", "JSONBIN_MASTER_KEY"}

        # Public page still works without storage configured.
        assert client.get("/").status_code == 200
        assert "test-master-key" not in client.get("/").text


def test_track_rate_limit(app_factory: Callable[[], Any]) -> None:
    with app_factory(extra={"TRACK_RATE_LIMIT_ATTEMPTS": "3", "TRACK_RATE_LIMIT_WINDOW_SECONDS": "60"}) as client:
        for _ in range(3):
            assert client.post("/api/track", json={}).status_code == 201
        blocked = client.post("/api/track", json={})
        assert blocked.status_code == 429
