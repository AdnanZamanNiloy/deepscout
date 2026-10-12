"""Build :class:`Document` objects from retrieved page records.

Retrieved pages arrive as dicts carrying ``raw_content``/``content``, ``url``
and ``title``. These helpers normalise them into the ``Document`` shape the
context filters and prompt formatters consume. Content is truncated to a
per-document cap for filtering safety; the cap is generous because it only
guards against pathologically large bodies.
"""
from __future__ import annotations

from typing import Dict, List

from app.common.config import get_settings
from app.common.models import Document


def _max_content_chars() -> int:
    return get_settings().research_max_content_chars


def pages_to_documents(pages: List[Dict]) -> List[Document]:
    """Convert retrieved page dicts into documents (title + source metadata)."""
    docs: List[Document] = []
    for page in pages or []:
        if not isinstance(page, dict):
            continue
        raw = page.get("raw_content")
        if raw is None:
            raw = page.get("content") or ""
        docs.append(
            Document(
                page_content=str(raw)[:_max_content_chars()],
                metadata={
                    "title": page.get("title", "") or "",
                    "source": page.get("url") or page.get("source") or "",
                },
            )
        )
    return docs


def sections_to_documents(sections: List[Dict]) -> List[Document]:
    """Convert ``{section_title, written_content}`` dicts into documents."""
    docs: List[Document] = []
    for section in sections or []:
        if not isinstance(section, dict):
            continue
        docs.append(
            Document(
                page_content=section.get("written_content", "") or "",
                metadata={"section_title": section.get("section_title", "") or ""},
            )
        )
    return docs
