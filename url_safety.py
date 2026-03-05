from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


_BLOCKED_HOSTS = {"localhost", "localhost.localdomain"}


def _is_blocked_ip(ip_text: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return any(
        (
            ip_obj.is_private,
            ip_obj.is_loopback,
            ip_obj.is_link_local,
            ip_obj.is_multicast,
            ip_obj.is_reserved,
            ip_obj.is_unspecified,
        )
    )


def validate_outbound_url(url: str, *, allowed_schemes: tuple[str, ...] = ("https",)) -> tuple[bool, str]:
    parsed = urlparse((url or "").strip())
    scheme = (parsed.scheme or "").lower()
    host = (parsed.hostname or "").strip().lower()

    if not scheme or not host:
        return False, "missing-scheme-or-host"
    if scheme not in allowed_schemes:
        return False, f"scheme-not-allowed:{scheme}"
    if parsed.username or parsed.password:
        return False, "userinfo-not-allowed"
    if host in _BLOCKED_HOSTS:
        return False, "blocked-host"

    try:
        if _is_blocked_ip(host):
            return False, "blocked-ip"
    except Exception:
        return False, "invalid-host"

    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except Exception:
        return False, "dns-resolution-failed"

    resolved_ips: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        if not sockaddr:
            continue
        ip_text = str(sockaddr[0]).strip()
        if ip_text:
            resolved_ips.add(ip_text)

    if not resolved_ips:
        return False, "no-dns-results"

    for ip_text in resolved_ips:
        if _is_blocked_ip(ip_text):
            return False, f"blocked-resolved-ip:{ip_text}"

    return True, "ok"


def validate_response_redirect_chain(response, *, allowed_schemes: tuple[str, ...] = ("https",)) -> tuple[bool, str]:
    urls: list[str] = []
    for item in getattr(response, "history", []) or []:
        url = getattr(item, "url", "")
        if url:
            urls.append(url)

    final_url = getattr(response, "url", "")
    if final_url:
        urls.append(final_url)

    for url in urls:
        ok, reason = validate_outbound_url(url, allowed_schemes=allowed_schemes)
        if not ok:
            return False, f"{reason}:{url}"

    return True, "ok"
