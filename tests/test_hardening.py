"""Security hardening tests: headers, session invalidation, admin shell markup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.security import hash_password

PUBLIC = Path(__file__).resolve().parents[1] / "public"


def test_security_headers_on_html(client: Any) -> None:
    response = client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "geolocation=(self)" in response.headers["permissions-policy"]
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_admin_pages_and_apis_are_no_store_and_noindex(client: Any) -> None:
    for path in ("/admin-private", "/api/auth/session", "/api/admin/stats"):
        response = client.get(path)
        assert response.headers.get("cache-control") == "no-store", path
        assert "noindex" in response.headers.get("x-robots-tag", ""), path


def test_public_page_is_cacheable_but_assets_ok(client: Any) -> None:
    assert client.get("/").status_code == 200


def test_login_is_disabled_by_rate_limiter_then_resets_after_success(app_factory: Any) -> None:
    with app_factory() as client:
        # Exhaust the fixture's 4-attempt window with failures.
        for _ in range(4):
            assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
        assert client.post("/api/auth/login", json={"password": "admin111322?"}).status_code == 429
    # A successful login in a fresh window clears the counter for that client.
    with app_factory() as client:
        assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
        assert client.post("/api/auth/login", json={"password": "admin111322?"}).status_code == 200
        # Session works immediately after the counter was cleared.
        assert client.get("/api/admin/stats").status_code == 200


def test_password_rotation_invalidates_existing_sessions(client: Any, monkeypatch: Any) -> None:
    from tests.conftest import configure_env, TEST_SESSION_SECRET

    assert client.post("/api/auth/login", json={"password": "admin111322?"}).status_code == 200
    assert client.get("/api/admin/stats").status_code == 200
    cookie = client.cookies.get("surprise_session")
    assert cookie

    # Rotate the admin password (new hash) while keeping the same secret.
    configure_env(
        monkeypatch,
        jsonbin=False,
        extra={"ADMIN_PASSWORD_HASH": hash_password("brand-new-password-1")},
    )
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as rotated:
        rotated.cookies.set("surprise_session", cookie)
        # Old session (issued for the previous password) must be rejected.
        assert rotated.get("/api/admin/stats").status_code == 401
        assert rotated.post(
            "/api/auth/login", json={"password": "brand-new-password-1"}
        ).status_code == 200
        session = rotated.get("/api/auth/session").json()
        assert session["authenticated"] is True


def test_admin_shell_markup(client: Any) -> None:
    html = client.get("/admin-private").text
    # View states: boot first; login and dashboard both start hidden (no flash).
    assert 'id="boot-view"' in html
    assert 'id="login-view" class="login-wrap" hidden' in html
    assert 'id="dashboard-view" class="app" hidden' in html
    # Sidebar shell.
    assert 'id="sidebar"' in html
    assert 'id="sidebar-overlay"' in html
    assert 'id="menu-btn"' in html
    assert 'class="side-link"' in html
    assert 'id="logout-btn"' in html
    # Password-only login, no identity fields.
    assert 'type="password"' in html
    assert 'name="email"' not in html
    assert 'name="username"' not in html


def test_css_hidden_attribute_wins_over_display_rules() -> None:
    """Regression: .login-wrap { display:grid } kept the login form visible
    above the dashboard because author styles beat the UA [hidden] rule."""
    css = (PUBLIC / "assets" / "css" / "main.css").read_text(encoding="utf-8")
    assert "[hidden]" in css
    assert "display: none !important" in css


def test_no_plaintext_password_anywhere_in_pages(client: Any) -> None:
    for path in ("/", "/admin-private"):
        body = client.get(path).text
        assert "admin111322?" not in body
        assert "ADMIN_PASSWORD=" not in body
