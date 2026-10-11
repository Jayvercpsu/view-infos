"""Public page and static asset tests."""

from __future__ import annotations

from typing import Any


def test_surprise_page_renders(client: Any) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    assert "SURPRISE" in html
    assert "🎉" in html
    assert "privacy" in html.lower()
    assert "/assets/js/surprise.js" in html
    assert "no-store" in response.headers.get("cache-control", "")


def test_surprise_page_has_privacy_notice(client: Any) -> None:
    html = client.get("/").text
    assert "Privacy notice" in html
    assert "contacts" in html.lower()  # explicit "we do not collect" statement
    assert "location prompt" in html.lower()  # GPS only via browser permission
    # Footer opt-out buttons were removed from the public page.
    assert "Delete my visit record" not in html
    assert "Share precise location" not in html


def test_surprise_page_is_responsive_and_accessible(client: Any) -> None:
    html = client.get("/").text
    assert 'name="viewport"' in html
    assert "prefers-reduced-motion" not in html  # handled in CSS, not inline
    assert 'class="skip-link"' in html
    assert 'lang="en"' in html


def test_admin_page_is_password_only(client: Any) -> None:
    response = client.get("/admin-private")
    assert response.status_code == 200
    html = response.text
    assert 'type="password"' in html
    assert 'name="email"' not in html
    assert 'name="username"' not in html
    assert "noindex" in html


def test_static_assets_served(client: Any) -> None:
    for path in (
        "/assets/css/main.css",
        "/assets/css/surprise.css",
        "/assets/css/admin.css",
        "/assets/js/confetti.js",
        "/assets/js/surprise.js",
        "/assets/js/admin.js",
        "/favicon.svg",
    ):
        response = client.get(path)
        assert response.status_code == 200, path


def test_admin_page_loads_dashboard_markup(client: Any) -> None:
    html = client.get("/admin-private").text
    for expected in (
        "stat-total",
        "visitor-rows",
        "login-form",
        "logout-btn",
        "empty-state",
        "error-state",
        "table-state",
    ):
        assert expected in html, expected


def test_pages_do_not_embed_secrets(client: Any) -> None:
    for path in ("/", "/admin-private"):
        body = client.get(path).text.lower()
        assert "test-master-key" not in body
        assert "admin111322?" not in body
        assert "session_secret" not in body


def test_unknown_api_route_is_404(client: Any) -> None:
    assert client.get("/api/definitely-not-a-route").status_code == 404
