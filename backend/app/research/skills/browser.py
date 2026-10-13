"""Page-scraping skill.

Scrapes URLs the retrieval layer did not already pre-fetch, records the resulting
pages and images on the researcher, and selects the top images by score with
content-hash dedup.
"""
from __future__ import annotations

from typing import List

from app.research.scraping.extract import get_image_hash
from app.research.scraping.scraper import scrape_urls
from app.common.streaming import stream_output
from app.research.workers import WorkerPool


class PageScraper:
    """Scrape content for the owning researcher."""

    def __init__(self, researcher):
        self.researcher = researcher
        self.worker_pool = WorkerPool(
            researcher.cfg.max_scraper_workers,
            researcher.cfg.scraper_rate_limit_delay,
        )

    async def browse_urls(self, urls: List[str]) -> List[dict]:
        """Scrape ``urls``, recording pages and top images on the researcher."""
        if self.researcher.verbose:
            await stream_output(
                "logs",
                "scraping_urls",
                f"Scraping content from {len(urls)} URLs...",
                self.researcher.sink,
            )

        scraped_content, images = await scrape_urls(
            urls, self.researcher.cfg, self.worker_pool
        )
        self.researcher.add_research_sources(scraped_content)
        new_images = self.select_top_images(images, k=4)
        self.researcher.add_research_images(new_images)

        if self.researcher.verbose:
            await stream_output(
                "logs",
                "scraping_content",
                f"Scraped {len(scraped_content)} pages of content",
                self.researcher.sink,
            )
            await stream_output(
                "logs",
                "scraping_images",
                f"Selected {len(new_images)} new images from {len(images)} total images",
                self.researcher.sink,
            )
        return scraped_content

    def select_top_images(self, images: List[dict], k: int = 2) -> List[str]:
        """Select top-scoring unique images by content hash (no repeats)."""
        unique_images: List[str] = []
        seen_hashes = set()
        current = self.researcher.get_research_images()

        for img in sorted(images, key=lambda im: im["score"], reverse=True):
            img_hash = get_image_hash(img["url"])
            if img_hash and img_hash not in seen_hashes and img["url"] not in current:
                seen_hashes.add(img_hash)
                unique_images.append(img["url"])
                if len(unique_images) == k:
                    break
        return unique_images
