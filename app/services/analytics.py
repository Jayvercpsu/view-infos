"""Build privacy-conscious visitor analytics records.

What is collected (and why):
* timestamp (UTC)        — count visits and see activity over time
* IP address             — approximate analytics + basic abuse detection
* approximate location   — coarse geography from trusted proxy headers only
* browser / OS / device  — understand the audience; no fingerprinting
* referrer               — see where traffic comes from

What is NEVER collected: contacts, SMS, photos, precise GPS (unless the
visitor explicitly opts in through the browser permission prompt), cookies
beyond the HttpOnly session cookie used for admin auth, or any client-supplied
IP/forwarded header that the trusted proxy has not overwritten.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import Request

TRUSTED_CLIENT_IP_HEADER = "x-real-ip"  # set/overwritten by Vercel Proxy
_TRUSTED_GEO_HEADERS = {
    "country": "x-vercel-ip-country",
    "region": "x-vercel-ip-country-region",
    "city": "x-vercel-ip-city",
}

# --- user-agent parsing (small, dependency-free, best effort) --------------

_BROWSERS = (
    ("Edg", "Edge"),
    ("OPR", "Opera"),
    ("Opera", "Opera"),
    ("SamsungBrowser", "Samsung Internet"),
    ("Firefox", "Firefox"),
    ("Chrome", "Chrome"),
    ("Safari", "Safari"),
    ("curl", "curl"),
    ("python", "Python client"),
)

_SYSTEMS = (
    ("Windows NT 10", "Windows 10+"),
    ("Windows NT", "Windows"),
    ("Android", "Android"),
    ("iPhone", "iOS"),
    ("iPad", "iPadOS"),
    ("Mac OS X", "macOS"),
    ("CrOS", "ChromeOS"),
    ("Linux", "Linux"),
)


@dataclass(frozen=True, slots=True)
class UserAgentInfo:
    browser: str
    os: str
    device: str  # mobile | tablet | desktop | unknown


def parse_user_agent(user_agent: str | None) -> UserAgentInfo:
    ua = (user_agent or "").strip()
    if not ua:
        return UserAgentInfo("Unknown", "Unknown", "unknown")

    lowered = ua.lower()
    if "bot" in lowered or "crawler" in lowered or "spider" in lowered:
        return UserAgentInfo("Bot", "Bot", "unknown")

    browser = "Other"
    for token, name in _BROWSERS:
        if token.lower() in lowered:
            # Safari also matches Chrome; prefer Chrome/Edge when present.
            browser = name
            break

    os_name = "Other"
    for token, name in _SYSTEMS:
        if token.lower() in lowered:
            os_name = name
            break

    if "ipad" in lowered or "playbook" in lowered or (
        "tablet" in lowered and "mobile" not in lowered
    ):
        device = "tablet"
    elif "mobile" in lowered or "android" in lowered or "iphone" in lowered or "ipod" in lowered:
        device = "mobile"
    elif os_name in {"Windows", "Windows 10+", "macOS", "Linux", "ChromeOS"}:
        device = "desktop"
    else:
        device = "unknown"

    return UserAgentInfo(browser, os_name, device)


# --- IP handling -----------------------------------------------------------

def _client_ip_from_socket(request: Request) -> str | None:
    client = request.client
    if client and client.host:
        return client.host
    return None


def extract_client_ip(request: Request, *, trust_proxy_headers: bool) -> str | None:
    """Return the client IP, trusting only proxy headers we control.

    `x-real-ip` is set (and overwritten) by Vercel's proxy, so it is safe on
    Vercel. Client-sent X-Forwarded-For is deliberately NOT trusted: anyone
    can forge it.
    """
    if trust_proxy_headers:
        candidate = request.headers.get(TRUSTED_CLIENT_IP_HEADER)
        if candidate:
            candidate = candidate.strip()
            try:
                return str(ipaddress.ip_address(candidate))
            except ValueError:
                pass
    return _client_ip_from_socket(request)


def anonymize_ip(ip: str | None) -> str | None:
    """Mask the host part: /24 for IPv4, /48 for IPv6. Returns None if invalid."""
    if not ip:
        return None
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return None
    if addr.version == 4:
        network = ipaddress.ip_network(f"{addr}/24", strict=False)
    else:
        network = ipaddress.ip_network(f"{addr}/48", strict=False)
    return str(network)


def extract_geo(request: Request, *, trust_proxy_headers: bool) -> dict[str, str | None]:
    """Approximate IP-based location from trusted proxy headers only."""
    if not trust_proxy_headers:
        return {"country": None, "region": None, "city": None}
    geo: dict[str, str | None] = {}
    for key, header in _TRUSTED_GEO_HEADERS.items():
        raw = request.headers.get(header)
        if raw:
            value = raw.strip()
            # Guard against absurd values; keep it short and printable.
            value = re.sub(r"[^\w\s.\-']", " ", value)[:64].strip()
            geo[key] = value or None
        else:
            geo[key] = None
    return geo


def extract_referrer(request: Request) -> str | None:
    referrer = request.headers.get("referer") or request.headers.get("referrer")
    if not referrer:
        return None
    referrer = referrer.strip()
    if not referrer or len(referrer) > 300:
        return None
    return referrer


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_iso_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def build_visit_record(
    request: Request,
    *,
    trust_proxy_headers: bool,
    anonymize: bool,
    user_agent_override: str | None = None,
    ip_override: str | None = None,
) -> dict[str, Any]:
    """Assemble a single visit record from trusted server-side signals."""
    ip = ip_override if ip_override is not None else extract_client_ip(
        request, trust_proxy_headers=trust_proxy_headers
    )
    stored_ip = anonymize_ip(ip) if anonymize else ip
    ua_source = user_agent_override if user_agent_override is not None else request.headers.get("user-agent")
    ua = parse_user_agent(ua_source)
    geo = extract_geo(request, trust_proxy_headers=trust_proxy_headers)
    referrer = extract_referrer(request)

    record: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "ts": utc_now_iso(),
        "ip": stored_ip,
        "ip_anonymized": bool(anonymize and ip),
        "country": geo["country"],
        "region": geo["region"],
        "city": geo["city"],
        "browser": ua.browser,
        "os": ua.os,
        "device": ua.device,
        "referrer": referrer,
        "user_agent": (ua_source or "")[:256] or None,
        "lat": None,
        "lon": None,
        "geo_opt_in": False,
    }
    return record


def sanitize_precise_geo(latitude: Any, longitude: Any) -> tuple[float, float] | None:
    """Validate opt-in GPS coordinates; rounded to 3 decimals (~110 m)."""
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return round(lat, 3), round(lon, 3)
