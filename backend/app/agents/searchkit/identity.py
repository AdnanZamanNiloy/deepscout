"""Source identity: who published a result, as distinct from who retrieved it.

A retrieval engine is not a publisher. `searxng:google cse` says which INDEX
answered a query; it says nothing about who wrote the page, and rendering it as
the source label made every Google-CSE hit look like it came from a publisher
called "google cse" — including papers on arxiv.org and mdpi.com.

This module produces the identity half of that pair, and nothing else:

    source_identity("https://WWW.Arxiv.ORG/pdf/2301.1") -> ("arxiv.org", "")

Rules, all of which exist because a real result forced them:

  * `www.` is stripped, so `www.example.com` and `example.com` are one source.
  * Port, userinfo and case are stripped: `user:pw@Example.COM:8443` and
    `example.com` are one source.
  * Subdomains are KEPT (`news.bbc.co.uk` stays itself) because the hostname is
    the canonical identity of the page; collapsing to a registrable domain is a
    different question and lives in `registrable_domain`.
  * Malformed input yields "" rather than raising, so one junk URL can never
    take down a search pass.
  * `publisher` is only ever a name the PROVIDER stated. It is never derived
    from the hostname (`example.com` is not "Example") and never taken from the
    retrieval engine. No name means "" and the UI falls back to the domain.
"""

from __future__ import annotations

import re

from app.agents.sources.urls import extract_domain


# A hostname is a dot-separated sequence of LDH labels. Used to reject the junk
# that urlparse happily returns a netloc for ("", "javascript:alert(1)", "..").
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

# scheme:// with no host, e.g. "https:///path" or a bare "not a url".
_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")

# Publishers arrive in wildly inconsistent shapes. These are the ones we accept,
# because dropping them means falling back to the domain, which is always safe.
_PUBLISHER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .,'&()\-/]{1,80}$")


def canonical_source_domain(url: str) -> str:
    """Canonical hostname for a result URL, or "" when there isn't one.

    Stricter than `extract_domain`: that helper is happy to return whatever
    string followed the scheme, which is fine for ranking but wrong for a label
    a human reads. This additionally rejects non-hostnames, so a malformed URL
    degrades to "" instead of rendering "javascript:alert(1)" as a source.
    """
    text = (url or "").strip()
    if not text:
        return ""
    if not _SCHEME_RE.match(text):
        # urlparse treats "example.com/x" as a path, so a missing scheme would
        # silently yield an empty host. Accept a bare host rather than lose it.
        if "/" not in text and _HOSTNAME_RE.match(text.lower()):
            return extract_domain(f"https://{text}")
        return ""
    host = extract_domain(text)
    if not host or not _HOSTNAME_RE.match(host):
        return ""
    return host


def normalize_publisher(raw: object) -> str:
    """Clean a provider-supplied publisher/journal name, or "" if unusable.

    Defensive on purpose: this value ends up in the UI as a label, and these
    strings come from third-party feeds. Collapsing whitespace, trimming, and
    rejecting anything that is not a short name-like string keeps a 4 KB HTML
    blob or a "None"/"null" sentinel out of the source line.
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    # Common sentinels that are technically strings but mean "unknown".
    if text.lower() in {"none", "null", "n/a", "na", "unknown", "-", "undefined"}:
        return ""
    if not _PUBLISHER_RE.match(text):
        return ""
    return text


def source_identity(url: str, publisher: object = None) -> tuple[str, str]:
    """`(source_domain, publisher)` for a result URL plus optional publisher.

    The single entry point every provider mapper uses, so the fields cannot drift
    apart between SearXNG, arXiv, Crossref and Wikipedia.
    """
    return canonical_source_domain(url), normalize_publisher(publisher)