from __future__ import annotations

from app.core.logging import get_logger

from dataclasses import dataclass
from html.parser import HTMLParser
import html
import httpx
import re
from typing import List, Optional

from app.agents.reliability import retry_after_from_headers
from app.agents.retrieval_health import classify_fetch_failure
from app.agents.sources import extract_domain as _host

logger = get_logger(__name__)


_WIKI_USER_AGENT = "DeepScout-research/1.0 (personal research assistant)"


_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)


MAX_FETCH_BYTES = 3_000_000


_READABLE_TYPES = ("text/html", "text/plain", "application/xhtml", "application/pdf",
                   "application/json", "text/xml", "application/xml")


class _VisibleTextExtractor(HTMLParser):
    """Extract readable text while dropping non-visible markup."""

    _SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg",
                            "nav", "footer", "form", "aside"})
    _BLOCK_TAGS = frozenset({
        "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5",
        "h6", "section", "article", "header", "footer", "nav", "aside",
        "main", "figure", "figcaption", "table", "tr", "td", "th",
        "blockquote", "pre", "hr",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._chunks: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        name = (tag or "").lower()
        if name in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if name in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        name = (tag or "").lower()
        if name in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if name in self._BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data or not data.strip():
            return
        self._chunks.append(data.strip() + " ")

    def text(self) -> str:
        return re.sub(r"[ \t\f\v]+", " ", "".join(self._chunks)).strip()


def _clean_html(raw: str, max_chars: int = 12000) -> str:
    """HTML -> readable text.

    The character cap rose from 6000 to 12000: the summarizer now chunks long
    documents, so truncating the source at 6000 characters was discarding
    material the extractor could use. Fetch-side caps still bound memory.
    """
    try:
        extractor = _VisibleTextExtractor()
        extractor.feed(raw or "")
        extractor.close()
        text = extractor.text()
    except Exception as exc:
        logger.warning("[Search] HTML clean failed, using regex fallback: %s", exc, exc_info=exc)
        text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html.unescape(text)
    text = re.sub(r"\n[ \t]*\n+", "\n\n", text)
    text = re.sub(r"[ \t\f\v]{2,}", " ", text).strip()
    return text[:max_chars]


BLOCK_PAGE_PHRASES = (
    "access denied",
    "verify you are human",
    "enable javascript",
    "please complete the security check",
    "unusual traffic from your computer network",
    "are you a robot",
    "request blocked",
)


def _looks_like_block_page(text: str) -> bool:
    lowered = (text or "").lower()
    return len(lowered) < 600 and any(p in lowered for p in BLOCK_PAGE_PHRASES)


def _extract_pdf_text(content: bytes, url: str) -> str:
    """Best-effort first pages of a PDF. PyMuPDF is optional."""
    try:
        try:
            import pymupdf
        except ImportError:
            import fitz as pymupdf
    except ImportError:
        logger.warning("[Search] PyMuPDF missing, skipping PDF: %s", url[:80])
        return ""
    try:
        pages = []
        with pymupdf.open(stream=content, filetype="pdf") as doc:
            for page in doc[:8]:
                pages.append(page.get_text())
        return re.sub(r"\s+", " ", "\n".join(pages)).strip()[:12000]
    except Exception as exc:
        logger.warning("[Search] PDF extract failed for %s: %s", url[:80], exc, exc_info=exc)
        return ""










FETCH_RETRY_AFTER_CAP_SEC = 6.0


def _retry_after_header(response) -> float:
    """Parse a Retry-After header (seconds form) from a response, capped.

    Delegates to the shared parser so the search-provider and page-fetch retry
    paths cannot disagree about what Retry-After means, and bounds it to this
    loop's own backoff ceiling.
    """
    headers = getattr(response, "headers", None)
    value = retry_after_from_headers(headers, cap=FETCH_RETRY_AFTER_CAP_SEC)
    return 0.0 if value is None else value


@dataclass
class FetchOutcome:
    """Structured result of one page fetch.

    The old `(text, last_modified)` tuple collapsed every failure — 403, 429,
    timeout, 404 — into the same empty string, which is exactly why a blocked
    host could not be told apart from a page with no text and why the same
    wall was re-paid for on every pass. The status/reason travels with the
    text so the caller can cool the host, skip the URL and count the failure.
    """

    text: str = ""
    last_modified: str = ""
    status: Optional[int] = None
    reason: str = "ok"
    attempts: int = 0
    retry_after: float = 0.0

    @property
    def ok(self) -> bool:
        return bool(self.text)


async def _fetch_once(client: httpx.AsyncClient, url: str) -> FetchOutcome:
    """One raw fetch: returns text + the HTTP status/reason that produced it.

    Content-type filtering and block-page detection still yield empty text,
    but they now carry a reason so the caller does not cool a host for
    returning, say, an unreadable PDF.
    """
    try:
        r = await client.get(url, follow_redirects=True)
    except Exception as exc:
        reason = classify_fetch_failure(None, exc)
        logger.warning("[Search] fetch %s failed (%s): %s", url[:80], reason, exc)
        return FetchOutcome(status=None, reason=reason, attempts=1)

    status = int(getattr(r, "status_code", 0) or 0)
    if not r.is_success:
        reason = classify_fetch_failure(status)
        logger.debug("[Search] fetch %s returned %s (%s)", url[:80], status, reason)
        return FetchOutcome(status=status, reason=reason, attempts=1,
                            retry_after=_retry_after_header(r))

    content_type = str(r.headers.get("content-type", "") or "").lower()
    last_modified = str(r.headers.get("last-modified", "") or "")

    is_pdf = "application/pdf" in content_type or url.lower().split("?")[0].endswith(".pdf")
    if not is_pdf and content_type and not any(t in content_type for t in _READABLE_TYPES):
        logger.debug("[Search] skipping unreadable content-type %s for %s", content_type, url[:60])
        return FetchOutcome(status=status, reason="other", attempts=1)

    body = r.content
    if len(body) > MAX_FETCH_BYTES:
        logger.warning(
            "[Search] truncating oversized response (%d bytes) from %s",
            len(body), url[:80],
        )
        body = body[:MAX_FETCH_BYTES]

    if is_pdf:
        return FetchOutcome(text=_extract_pdf_text(body, url),
                            last_modified=last_modified, status=status,
                            reason="ok", attempts=1)

    try:
        raw = body.decode(r.encoding or "utf-8", errors="replace")
    except (LookupError, UnicodeDecodeError):
        raw = body.decode("utf-8", errors="replace")

    text = _clean_html(raw)
    if _looks_like_block_page(text):
        logger.warning("[Search] block page detected, dropping: %s", url[:80])
        return FetchOutcome(text="", last_modified=last_modified,
                            status=status, reason="forbidden", attempts=1)
    return FetchOutcome(text=text, last_modified=last_modified,
                        status=status, reason="ok", attempts=1)


def _domain_of(url: str) -> str:
    """Registrable-domain key for cooldown/failure accounting.

    Uses the shared `registrable_domain` so two hosts under one publisher
    (blog.example.com / www.example.com) cool down together.
    """
    try:
        from app.core.evidence_grade import registrable_domain

        return registrable_domain(url)
    except Exception:  # pragma: no cover - defensive
        return _host(url)
