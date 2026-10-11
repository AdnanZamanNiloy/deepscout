"""Scrape a batch of URLs into usable page records.

Deduplicates the input, rejects unsafe targets, fetches concurrently (bounded by
the section's worker pool), and applies the content guards so a block page,
word-list dump or unextracted PDF is treated as a fetch failure rather than
being ingested as article text. Every returned record carries
``url``/``raw_content``/``image_urls``/``title``; failures carry
``raw_content=None`` and are dropped by :func:`scrape_urls`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.research.config import ResearchConfig
from app.research.scraping.fetcher import (
    PageFetcher,
    _looks_like_block_page,
    _looks_like_unextracted_pdf,
    _looks_like_word_list,
)
from app.research.url_security import UnsafeURLError, validate_url
from app.research.workers import WorkerPool

logger = logging.getLogger(__name__)


class Scraper:
    """Fetch and extract content for a list of URLs concurrently."""

    def __init__(
        self,
        urls: List[str],
        user_agent: str,
        worker_pool: WorkerPool,
    ):
        unique_urls = list(dict.fromkeys(urls))
        duplicates_removed = len(urls) - len(unique_urls)
        self.urls = unique_urls
        self.user_agent = user_agent
        self.worker_pool = worker_pool
        if duplicates_removed > 0:
            logger.info(
                "Removed %d duplicate URL(s). Scraping %d unique URLs instead of %d.",
                duplicates_removed,
                len(unique_urls),
                len(urls),
            )

    async def run(self) -> List[Dict[str, Any]]:
        """Fetch every URL concurrently and return the successful records."""
        headers = {"User-Agent": self.user_agent}
        async with httpx.AsyncClient(headers=headers) as client:
            fetcher = PageFetcher(client)
            contents = await asyncio.gather(
                *(self._extract(fetcher, url) for url in self.urls)
            )
        # A buggy or cancelled fetch can yield None/non-dict; index those out so
        # one bad row does not drop the good ones.
        return [
            content
            for content in contents
            if isinstance(content, dict) and content.get("raw_content") is not None
        ]

    async def _extract(self, fetcher: PageFetcher, link: str) -> Optional[Dict[str, Any]]:
        async with self.worker_pool.throttle():
            try:
                try:
                    validate_url(link)
                except UnsafeURLError as e:
                    logger.warning("Skipping unsafe URL %s: %s", link, e)
                    return {"url": link, "raw_content": None, "image_urls": [], "title": ""}

                content, image_urls, title = await fetcher.fetch(link)

                if not content or len(content) < 100:
                    return self._reject(link, title, "Content too short or empty")
                if _looks_like_block_page(content):
                    return self._reject(link, title, "Anti-bot/challenge page detected")
                if _looks_like_word_list(content):
                    return self._reject(link, title, "Word-list-like content detected")
                if _looks_like_unextracted_pdf(content):
                    return self._reject(link, title, "Unextracted PDF binary detected")

                return {
                    "url": link,
                    "raw_content": content,
                    "image_urls": image_urls,
                    "title": title,
                }
            except Exception as e:
                logger.error("Error processing %s: %s", link, e)
                return {"url": link, "raw_content": None, "image_urls": [], "title": ""}

    @staticmethod
    def _reject(link: str, title: str, reason: str) -> Dict[str, Any]:
        """Treat a fetched-but-unusable page as a fetch failure."""
        logger.warning("%s for %s, treating as fetch failure", reason, link)
        return {"url": link, "raw_content": None, "image_urls": [], "title": title}


async def scrape_urls(
    urls: List[str], cfg: ResearchConfig, worker_pool: WorkerPool
) -> Tuple[List[Dict[str, Any]], List[dict]]:
    """Scrape ``urls`` and return ``(scraped_content, images)``."""
    scraped_data: List[Dict[str, Any]] = []
    images: List[dict] = []
    user_agent = cfg.user_agent if cfg else "Mozilla/5.0"
    try:
        scraper = Scraper(urls, user_agent, worker_pool=worker_pool)
        scraped_data = await scraper.run()
        for item in scraped_data:
            if not isinstance(item, dict):
                continue
            image_urls = item.get("image_urls")
            if image_urls:
                images.extend(image_urls)
    except Exception as e:
        logger.error("Error in scrape_urls: %s", e, exc_info=e)
    return scraped_data, images
