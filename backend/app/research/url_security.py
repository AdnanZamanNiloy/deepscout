"""URL safety checks for fetching.

Untrusted URLs (from search results, user-provided source lists, or model
output) must not be able to reach internal hosts, cloud metadata endpoints, or
local files. :func:`validate_url` enforces http/https and a public-address
resolution before any request is made.

Operators who intentionally fetch internal or self-hosted resources can set
``ALLOW_PRIVATE_URLS=true`` (or pass ``allow_private=True``).
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")


class UnsafeURLError(ValueError):
    """Raised when a URL is rejected by the SSRF / local-file protections."""


def _private_urls_allowed() -> bool:
    from app.common.config import get_settings

    return bool(get_settings().allow_private_urls)


def _is_disallowed_ip(ip) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_url(url: str, *, allow_private: bool | None = None) -> str:
    """Validate that ``url`` is safe to fetch and return it unchanged.

    Raises :class:`UnsafeURLError` for a disallowed scheme, a missing host, or a
    host that resolves to a non-public address.
    """
    if not isinstance(url, str) or not url.strip():
        raise UnsafeURLError("URL must be a non-empty string.")

    parsed = urlparse(url.strip())

    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeURLError(
            f"URL scheme {scheme or '(none)'!r} is not allowed; "
            "only http and https URLs may be fetched."
        )

    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL must include a valid host.")

    if allow_private is None:
        allow_private = _private_urls_allowed()
    if allow_private:
        return url

    try:
        addrinfo = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve host {host!r}: {exc}") from exc

    for info in addrinfo:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError as exc:
            raise UnsafeURLError(
                f"Host {host!r} resolved to an invalid address {ip_str!r}."
            ) from exc
        if _is_disallowed_ip(ip):
            raise UnsafeURLError(
                f"URL host {host!r} resolves to a non-public address ({ip_str}); "
                "set ALLOW_PRIVATE_URLS=true to allow internal targets."
            )

    return url
