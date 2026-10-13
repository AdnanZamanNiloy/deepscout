"""Pre-flight safety check for URLs the pipeline is about to fetch.

Fetched URLs can arrive from search clients, model output, or a caller-supplied
source list, so none of them are trusted. Before any socket is opened,
:func:`validate_url` rejects anything that could reach a loopback/private host,
a link-local cloud-metadata address, or a local file, and rejects non-HTTP
schemes outright. The DNS lookup performed here is deliberately part of the
check rather than left to the HTTP client, which is what closes the obvious
unauthenticated SSRF and local-read paths.

A deployment that really does need to reach an internal host can opt out by
setting ``ALLOW_PRIVATE_URLS=true`` or by passing ``allow_private=True`` to a
single call; the opt-out bypasses the address check only, never the scheme
check.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")

# Address classes that must never be contacted from an untrusted URL. The tuple
# is ordered purely for readability; membership is what matters.
_UNSAFE_IP_FLAGS = (
    "is_private",
    "is_loopback",
    "is_link_local",
    "is_reserved",
    "is_multicast",
    "is_unspecified",
)


class UnsafeURLError(ValueError):
    """Raised when a URL is rejected by the SSRF / local-file protections."""


def _private_urls_allowed() -> bool:
    """Whether the configured run may contact internal addresses."""
    from app.common.config import get_settings

    return bool(get_settings().allow_private_urls)


def _is_disallowed_ip(ip) -> bool:
    """True when ``ip`` belongs to a class that must not be reached."""
    return any(getattr(ip, flag) for flag in _UNSAFE_IP_FLAGS)


def _resolved_addresses(host: str) -> list:
    """Every address ``host`` resolves to, or raise :class:`UnsafeURLError`.

    A name that does not resolve is treated as unsafe: this is a fetch guard,
    and a host we cannot vet is a host we do not contact.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Could not resolve host {host!r}: {exc}") from exc

    addresses = []
    for info in infos:
        raw = info[4][0]
        try:
            addresses.append(ipaddress.ip_address(raw))
        except ValueError as exc:
            raise UnsafeURLError(
                f"Host {host!r} resolved to an invalid address {raw!r}."
            ) from exc
    return addresses


def validate_url(url: str, *, allow_private: bool | None = None) -> str:
    """Return ``url`` unchanged if it is safe to fetch, else raise.

    Rejects a non-string/blank input, a scheme other than http/https, a URL
    with no host, and (unless private access is allowed) any host whose
    resolution includes a non-public address.
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

    for ip in _resolved_addresses(host):
        if _is_disallowed_ip(ip):
            raise UnsafeURLError(
                f"URL host {host!r} resolves to a non-public address ({ip}); "
                "set ALLOW_PRIVATE_URLS=true to allow internal targets."
            )

    return url
