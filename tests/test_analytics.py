"""Unit tests for analytics helpers (UA parsing, IP trust rules, geo opt-in)."""

from __future__ import annotations

from starlette.datastructures import Headers

from app.services.analytics import (
    anonymize_ip,
    extract_client_ip,
    extract_geo,
    parse_user_agent,
    sanitize_precise_geo,
)


class _FakeRequest:
    def __init__(self, headers: dict[str, str], client_host: str | None = "10.1.2.3") -> None:
        self.headers = Headers(headers)
        self.client = type("Client", (), {"host": client_host})()


def test_parse_user_agent_mobile_chrome_android() -> None:
    ua = (
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
    )
    info = parse_user_agent(ua)
    assert info.browser == "Chrome"
    assert info.os == "Android"
    assert info.device == "mobile"


def test_parse_user_agent_ipad_tablet() -> None:
    ua = (
        "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1"
    )
    info = parse_user_agent(ua)
    assert info.device == "tablet"
    assert info.os == "iPadOS"
    assert info.browser == "Safari"


def test_parse_user_agent_iphone_safari_is_mobile() -> None:
    info = parse_user_agent("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/605.1.15")
    assert info.device == "mobile"
    assert info.os == "iOS"
    assert info.browser == "Safari"


def test_parse_user_agent_windows_firefox() -> None:
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:127.0) Gecko/20100101 Firefox/127.0"
    info = parse_user_agent(ua)
    assert info.browser == "Firefox"
    assert info.os == "Windows 10+"
    assert info.device == "desktop"


def test_parse_user_agent_bot() -> None:
    info = parse_user_agent("Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)")
    assert info.browser == "Bot"
    assert info.device == "unknown"


def test_parse_user_agent_empty() -> None:
    info = parse_user_agent(None)
    assert info.browser == "Unknown"
    assert info.device == "unknown"


def test_client_ip_trusts_x_real_ip_only_when_enabled() -> None:
    request = _FakeRequest({"x-real-ip": "198.51.100.9", "x-forwarded-for": "6.6.6.6"})
    trusted = extract_client_ip(request, trust_proxy_headers=True)  # type: ignore[arg-type]
    assert trusted == "198.51.100.9"

    untrusted = extract_client_ip(request, trust_proxy_headers=False)  # type: ignore[arg-type]
    assert untrusted == "10.1.2.3"  # socket peer, not the spoofable header


def test_client_ip_rejects_garbage_header() -> None:
    request = _FakeRequest({"x-real-ip": "not-an-ip"})
    assert extract_client_ip(request, trust_proxy_headers=True) == "10.1.2.3"  # type: ignore[arg-type]


def test_anonymize_ip_masks_v4_and_v6() -> None:
    assert anonymize_ip("203.0.113.42") == "203.0.113.0/24"
    assert anonymize_ip("2001:db8:abcd:12::1") == "2001:db8:abcd::/48"
    assert anonymize_ip("garbage") is None
    assert anonymize_ip(None) is None


def test_extract_geo_uses_trusted_headers_only() -> None:
    request = _FakeRequest(
        {
            "x-vercel-ip-country": "PH",
            "x-vercel-ip-city": "Manila",
            "x-vercel-ip-country-region": "NCR",
        }
    )
    geo = extract_geo(request, trust_proxy_headers=True)  # type: ignore[arg-type]
    assert geo == {"country": "PH", "region": "NCR", "city": "Manila"}

    assert extract_geo(request, trust_proxy_headers=False) == {  # type: ignore[arg-type]
        "country": None,
        "region": None,
        "city": None,
    }


def test_sanitize_precise_geo_rounds_and_validates() -> None:
    assert sanitize_precise_geo(14.599512, 120.984222) == (14.6, 120.984)
    assert sanitize_precise_geo("14.599512", "120.984222") == (14.6, 120.984)
    assert sanitize_precise_geo(91, 0) is None
    assert sanitize_precise_geo(0, 181) is None
    assert sanitize_precise_geo(None, None) is None
    assert sanitize_precise_geo("abc", "def") is None
