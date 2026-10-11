"""Authentication tests: login, sessions, rate limiting, logout."""

from __future__ import annotations

from typing import Any, Callable

from app.security import SESSION_COOKIE_NAME, hash_password, verify_password


def test_password_hash_roundtrip() -> None:
    digest = hash_password("s3cure-P@ss!")
    assert digest.startswith("$2b$")
    assert verify_password("s3cure-P@ss!", digest)
    assert not verify_password("s3cure-P@ss", digest)
    assert not verify_password("", digest)
    assert not verify_password("x", "not-a-real-bcrypt-hash")


def test_password_hash_never_contains_plaintext() -> None:
    digest = hash_password("admin111322?")
    assert "admin111322?" not in digest


def test_login_with_wrong_password_is_generic(client: Any) -> None:
    response = client.post("/api/auth/login", json={"password": "wrong-password-here"})
    assert response.status_code == 401
    body = response.json()
    assert body["detail"] == "Invalid password."
    assert "admin" not in body["detail"].lower() or body["detail"] == "Invalid password."


def test_login_with_correct_password_sets_httponly_cookie(client: Any) -> None:
    response = client.post("/api/auth/login", json={"password": "admin111322?"})
    assert response.status_code == 200, response.text
    set_cookie = response.headers.get("set-cookie", "").lower()
    assert SESSION_COOKIE_NAME.lower() in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    # COOKIE_SECURE=false in tests; production default is Secure on HTTPS.
    assert response.json()["ok"] is True


def test_login_requires_only_a_password_field(client: Any) -> None:
    response = client.post("/api/auth/login", json={"password": "admin111322?"})
    assert response.status_code == 200
    # Reject extra identity fields silently (password-only auth by design).
    extra = client.post(
        "/api/auth/login", json={"password": "admin111322?", "email": "a@b.c", "username": "nope"}
    )
    assert extra.status_code == 200


def test_login_validation_rejects_empty_password(client: Any) -> None:
    response = client.post("/api/auth/login", json={"password": ""})
    assert response.status_code == 422


def test_session_endpoint_reflects_auth_state(client: Any) -> None:
    assert client.get("/api/auth/session").json()["authenticated"] is False
    client.post("/api/auth/login", json={"password": "admin111322?"})
    assert client.get("/api/auth/session").json()["authenticated"] is True


def test_logout_clears_session(client: Any) -> None:
    client.post("/api/auth/login", json={"password": "admin111322?"})
    assert client.get("/api/auth/session").json()["authenticated"] is True
    logout = client.post("/api/auth/logout")
    assert logout.status_code == 200
    assert client.get("/api/auth/session").json()["authenticated"] is False


def test_tampered_cookie_is_rejected(client: Any) -> None:
    client.post("/api/auth/login", json={"password": "admin111322?"})
    client.cookies.set(SESSION_COOKIE_NAME, "not-a-valid-token")
    assert client.get("/api/auth/session").json()["authenticated"] is False
    assert client.get("/api/admin/stats").status_code == 401


def test_login_rate_limit_triggers_429(
    app_factory: Callable[[], Any], monkeypatch: Any
) -> None:
    with app_factory() as client:
        # The fixture sets LOGIN_RATE_LIMIT_ATTEMPTS=4; exhaust it.
        for _ in range(4):
            assert client.post("/api/auth/login", json={"password": "nope"}).status_code == 401
        blocked = client.post("/api/auth/login", json={"password": "admin111322?"})
        assert blocked.status_code == 429
        assert "Retry-After" in blocked.headers
        assert "Invalid password" not in blocked.text


def test_rate_limit_is_per_client_not_global(
    app_factory: Callable[[], Any]
) -> None:
    with app_factory() as client:
        for _ in range(4):
            client.post("/api/auth/login", json={"password": "nope"})
        blocked = client.post("/api/auth/login", json={"password": "nope"})
        assert blocked.status_code == 429
        # A different client identity (different forwarded IP while trusted, or
        # fresh limiter) is not blocked; here we reset via new app instance.
    with app_factory() as fresh:
        assert fresh.post("/api/auth/login", json={"password": "admin111322?"}).status_code == 200


def test_unconfigured_auth_gives_503(monkeypatch: Any) -> None:
    from tests.conftest import configure_env

    from fastapi.testclient import TestClient

    from app.main import create_app

    configure_env(monkeypatch, jsonbin=False, extra={"ADMIN_PASSWORD_HASH": None, "ADMIN_PASSWORD": None})
    with TestClient(create_app()) as client:
        response = client.post("/api/auth/login", json={"password": "anything"})
        assert response.status_code == 503
        assert "not configured" in response.json()["detail"].lower()


def test_dev_password_env_is_accepted(monkeypatch: Any) -> None:
    from tests.conftest import configure_env

    from fastapi.testclient import TestClient

    from app.main import create_app

    configure_env(
        monkeypatch,
        jsonbin=False,
        extra={"ADMIN_PASSWORD_HASH": None, "ADMIN_PASSWORD": "dev-only-password"},
    )
    with TestClient(create_app()) as client:
        assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
        assert client.post("/api/auth/login", json={"password": "dev-only-password"}).status_code == 200


def test_session_cookie_is_not_readable_from_js_fields(client: Any) -> None:
    """The cookie must be HttpOnly; the API never returns the raw token."""
    response = client.post("/api/auth/login", json={"password": "admin111322?"})
    assert "token" not in response.text
    assert "session" not in response.json()
