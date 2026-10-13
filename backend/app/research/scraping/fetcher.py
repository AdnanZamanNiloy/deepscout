"""Fetch one page and extract its text, images and title.

A single httpx-backed fetcher serves the pipeline. The retrieval layer already
pre-fetches the pages the client reads, so
this only runs for URLs that still need fetching; one robust HTML path (with a
PDF route) covers those. Anti-bot pages, word-list dumps and unextracted PDF
binaries are detected and returned as empty content rather than being ingested
as if they were article text.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import httpx
from bs4 import BeautifulSoup

from app.research.scraping.extract import (
    clean_soup,
    extract_title,
    get_relevant_images,
    get_text_from_soup,
)

logger = logging.getLogger(__name__)

# Response codes worth one retry: rate limiting and transient server errors.
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
MAX_CONTENT_BYTES = 10 * 1024 * 1024  # Skip pages larger than 10MB

# A challenge/interstitial page answers with HTTP 200 and a real body, so only a
# textual tell distinguishes it from article text. These markers are the exact
# wording those pages ship; they are specific enough not to collide with normal
# prose. Only the beginning of a response is scanned.
_BLOCK_PAGE_MARKERS = (
    "anubis uses a proof-of-work scheme",
    "making sure you're not a bot",
    "checking your browser before accessing",
    "enable javascript and cookies to continue",
    "researchgate - temporarily unavailable",
    "please verify you are a human",
    "attention required! | cloudflare",
    "sorry, you have been blocked",
)
_BLOCK_PAGE_SCAN_PREFIX = 5_000

# A word-list dump is a long body of unpunctuated tokens: real prose, however
# dense, keeps sentence-ending marks far denser than this. Length alone is not
# a signal; the check needs both real size and the near-absence of punctuation.
# CJK terminators count so long-form CJK prose is not mistaken for a list.
_WORDLIST_MIN_LENGTH = 200_000
_WORDLIST_MAX_SENTENCE_DENSITY = 1 / 5000
_SENTENCE_TERMINATORS = frozenset(".!?。！？")

# PDF's own structural tokens, which ordinary prose does not contain. Two
# independent hits are enough to conclude the body is raw PDF bytes.
_PDF_STRUCTURE_MARKERS = ("endobj", "endstream", "/FlateDecode", "xref", "trailer")
_PDF_MIN_MARKER_HITS = 2


def _looks_like_block_page(text: str) -> bool:
    """True when the response opens with a known anti-bot/challenge page."""
    opening = text[:_BLOCK_PAGE_SCAN_PREFIX].lower()
    return any(marker in opening for marker in _BLOCK_PAGE_MARKERS)


def _looks_like_word_list(text: str) -> bool:
    """True when a large body has almost no sentence-ending punctuation."""
    if len(text) < _WORDLIST_MIN_LENGTH:
        return False
    terminators = sum(1 for ch in text if ch in _SENTENCE_TERMINATORS)
    return (terminators / len(text)) < _WORDLIST_MAX_SENTENCE_DENSITY


def _looks_like_unextracted_pdf(text: str) -> bool:
    """True when the body is raw PDF bytes rather than decoded text."""
    if text.startswith("%PDF-"):
        return True
    hits = sum(1 for marker in _PDF_STRUCTURE_MARKERS if marker in text)
    return hits >= _PDF_MIN_MARKER_HITS


class PageFetcher:
    """Fetch and extract a single URL.

    ``client`` is a shared :class:`httpx.AsyncClient` (with the run's user agent
    set) so connections are pooled across a section's fetches.
    """

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def fetch(
        self, url: str
    ) -> Tuple[str, List[dict], str]:
        """Return ``(content, image_urls, title)``; empty content on failure."""
        response = await self._get(url)
        if response is None:
            return "", [], ""
        try:
            content_type = response.headers.get("Content-Type", "").lower()
            if "pdf" in content_type:
                content = self._extract_pdf(response.content)
                return content, [], ""
            declared = None
            if "charset" in content_type:
                declared = response.encoding
            soup = BeautifulSoup(response.content, "lxml", from_encoding=declared)
            soup = clean_soup(soup)
            content = get_text_from_soup(soup)
            images = get_relevant_images(soup, url)
            title = extract_title(soup)
            return content, images, title
        except Exception as e:
            logger.error("Error parsing %s: %s", url, e)
            return "", [], ""

    async def _get(self, url: str) -> Optional[httpx.Response]:
        """GET the page, retrying once on transient failures."""
        for attempt in (1, 2):
            try:
                response = await self.client.get(url, timeout=10.0, follow_redirects=True)
            except Exception as e:
                logger.warning("Request failed for %s (attempt %s): %s", url, attempt, e)
                if attempt == 1:
                    continue
                return None

            if response.status_code in RETRYABLE_STATUS_CODES and attempt == 1:
                logger.warning("Got HTTP %s for %s, retrying once", response.status_code, url)
                continue

            if response.status_code >= 400:
                logger.warning("Got HTTP %s for %s, skipping", response.status_code, url)
                return None

            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    length_val = int(str(content_length).strip())
                except (TypeError, ValueError):
                    length_val = None
                if length_val is not None and length_val > MAX_CONTENT_BYTES:
                    logger.warning(
                        "Content too large for %s (%s bytes), skipping", url, content_length
                    )
                    return None

            return response
        return None

    @staticmethod
    def _extract_pdf(data: bytes) -> str:
        """Extract text from a PDF body, or ``""`` when extraction is unavailable."""
        try:
            import fitz  # PyMuPDF, optional at runtime

            with fitz.open(stream=data, filetype="pdf") as doc:
                return "\n".join(page.get_text() for page in doc)
        except Exception as e:
            logger.warning("PDF extraction failed: %s", e)
            return ""
