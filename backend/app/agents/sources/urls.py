from __future__ import annotations

from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import re

from app.core.logging import get_logger

logger = get_logger(__name__)


_TRACKING_PARAM_RE = re.compile(
    r"^(?:utm_|ga_|gclid$|fbclid$|mc_|ref$|ref_src$|referrer$|source$|"
    r"spm$|_hsenc$|_hsmi$|igshid$|si$|amp$)",
    re.IGNORECASE,
)


def extract_domain(url: str) -> str:
    """Lowercase host with a leading `www.` removed, or "" when unparseable."""
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return ""
    host = (parsed.netloc or "").lower().split("@")[-1]
    if ":" in host:
        host = host.split(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]
    return host


def canonical_url(url: str) -> str:
    """Collapse the many URLs that name one document into a single key.

    Strips tracking parameters, fragments, default ports, `amp` suffixes,
    duplicate slashes and a trailing slash, and normalizes the scheme to
    https. Without this, `?utm_source=...`, `#section`, and `http://` copies
    of the same page each consumed a separate fetch, a separate cache entry,
    and a separate slot in the "distinct sources" counts that gate the
    critic — inflating perceived source diversity with duplicates.
    """
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return raw
    if not parsed.netloc:
        return raw

    host = (parsed.netloc or "").lower().split("@")[-1]
    if host.endswith(":80") or host.endswith(":443"):
        host = host.rsplit(":", 1)[0]
    if host.startswith("www."):
        host = host[4:]

    path = re.sub(r"/{2,}", "/", parsed.path or "")
    if path.endswith("/amp"):
        path = path[:-4]
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]

    query_pairs = [
        (k, v)
        for k, v in parse_qsl(parsed.query or "", keep_blank_values=True)
        if not _TRACKING_PARAM_RE.match(k)
    ]
    query_pairs.sort()

    return urlunparse(("https", host, path, "", urlencode(query_pairs), ""))
