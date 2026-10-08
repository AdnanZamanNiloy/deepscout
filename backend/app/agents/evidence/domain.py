from __future__ import annotations

from typing import Iterable, Set

from app.agents.sources import LOW_TRUST_DOMAINS
from app.agents.sources import authority_score
from app.agents.sources import documentary_authority
from app.agents.sources import extract_domain as _extract_domain


LOW_QUALITY_DOMAINS: Set[str] = {
    "reddit.com", "quora.com", "zhihu.com", "baidu.com", "sohu.com",
    "csdn.net", "medium.com", "blogspot.com", "substack.com",
    "wordpress.com", "youtube.com", "youtu.be", "tiktok.com",
    "pinterest.com", "whatfix.com",
}


def extract_domain(url: str) -> str:
    return _extract_domain(url)


def is_high_quality_domain(
    url: str, blocked_domains: Iterable[str] = LOW_TRUST_DOMAINS, text: str = ""
) -> bool:
    """False for blocked or zero-authority hosts. Signature preserved: a
    caller-supplied blocklist is honoured on top of the registry.

    `text` is the page's own words. Passing it lets an UNREGISTERED domain be
    judged on documentary evidence rather than on its suffix: every unregistered
    `.com` scores 0.58, which clears `authority > 0.0` here, the verifier's 0.55
    gate and this module's own 0.55 fallback path — the fallback that engages on
    exactly the hard queries where junk matters most. With no text supplied the
    legacy suffix-based score is used unchanged.
    """
    domain = extract_domain(url)
    if not domain:
        return False
    for blocked in blocked_domains or ():
        blocked_value = str(blocked).lower().strip()
        if not blocked_value:
            continue
        if domain == blocked_value or domain.endswith(f".{blocked_value}"):
            return False
    if text:
        return documentary_authority(url, text) > 0.0
    return authority_score(url) > 0.0


def source_reliability_score(url: str) -> float:
    """0.0-1.0 authority for a URL (0.0 = never cite). Registry-backed."""
    return authority_score(url)
