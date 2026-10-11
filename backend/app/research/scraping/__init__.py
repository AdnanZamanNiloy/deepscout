"""Page scraping: fetching URLs that still need their content read."""
from __future__ import annotations

from app.research.scraping.extract import (
    clean_soup,
    extract_title,
    get_image_hash,
    get_relevant_images,
    get_text_from_soup,
)
from app.research.scraping.fetcher import PageFetcher
from app.research.scraping.scraper import Scraper, scrape_urls

__all__ = [
    "clean_soup",
    "extract_title",
    "get_image_hash",
    "get_relevant_images",
    "get_text_from_soup",
    "PageFetcher",
    "Scraper",
    "scrape_urls",
]
