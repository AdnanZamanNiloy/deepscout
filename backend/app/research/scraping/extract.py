"""HTML text extraction.

Given a parsed page, this module removes scripts/styles/navigation chrome and
then converts the remaining DOM into readable text: one line per block-level
element, with adjacent inline markup (a link, an emphasis) kept inside the line
it belongs to so sentences — and words — are never split. It also pulls out the
page title and a ranked shortlist of candidate images.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import List, Optional
from urllib.parse import parse_qs, urljoin, urlparse

import bs4
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Class names that almost always mark a leading/hero image on a page.
_HERO_CLASS_HINTS = ("header", "featured", "hero", "thumbnail", "main", "content")

# Size thresholds, largest first: the first band a (width, height) pair clears
# decides the score. Anything below the last band is treated as decoration.
_SIZE_BANDS = (
    ((2000, 1000), 3),
    ((1600, 800), 2),
    ((800, 500), 1),
    ((500, 300), 0),
)

# Chrome that carries no article text.
_STRIP_TAGS = ("script", "style", "footer", "header", "nav", "menu", "sidebar", "svg")
_DISALLOWED_CLASSES = frozenset(("nav", "menu", "sidebar", "footer"))


def _coerce_int(value) -> Optional[int]:
    """Best-effort integer from an HTML dimension attribute."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value[:-2] if value.lower().endswith("px") else value
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


# Public alias kept for callers that reference the parse helper by name.
def parse_dimension(value) -> Optional[int]:
    """Parse an HTML width/height value (handling plain numbers and 'px')."""
    return _coerce_int(value)


def _img_classes(img) -> List[str]:
    classes = img.get("class") or []
    if isinstance(classes, str):
        return classes.split()
    if isinstance(classes, (list, tuple, set)):
        return list(classes)
    return []


def _score_image(img) -> Optional[int]:
    """Relevance score for one ``<img>``, or ``None`` to skip it.

    A hero/featured class wins outright; otherwise the declared pixel size
    decides, and images too small to be meaningful are skipped.
    """
    if any(hint in _img_classes(img) for hint in _HERO_CLASS_HINTS):
        return 4
    width = _coerce_int(img.get("width"))
    height = _coerce_int(img.get("height"))
    if not (width and height):
        return 0
    for (min_w, min_h), score in _SIZE_BANDS:
        if width >= min_w and height >= min_h:
            return score
    return None


def get_relevant_images(soup: BeautifulSoup, url: str) -> List[dict]:
    """Extract candidate images (url + relevance score), highest score first."""
    if soup is None:
        return []
    try:
        scored: List[dict] = []
        for img in soup.find_all("img", src=True):
            src = urljoin(url, img["src"])
            if not src.startswith(("http://", "https://")):
                continue
            score = _score_image(img)
            if score is None:
                continue
            scored.append({"url": src, "score": score})
        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[:10]
    except Exception as exc:
        logger.error("Error in get_relevant_images: %s", exc)
        return []


def extract_title(soup: BeautifulSoup) -> str:
    """Extract the page title as plain text (``""`` when absent)."""
    title_tag = soup.title
    if not title_tag:
        return ""
    return title_tag.get_text(strip=True)


def get_image_hash(image_url: str) -> Optional[str]:
    """Hash of an image's filename + essential query params, for dedup."""
    try:
        parsed = urlparse(image_url)
        filename = parsed.path.rsplit("/", 1)[-1]
        cdn_source = parse_qs(parsed.query).get("url", [])
        return hashlib.md5((filename + "".join(cdn_source)).encode()).hexdigest()
    except Exception as exc:
        logger.error("Error calculating image hash for %s: %s", image_url, exc)
        return None


def clean_soup(soup: BeautifulSoup) -> BeautifulSoup:
    """Remove scripts, styles and navigation chrome from the soup."""
    for tag in soup.find_all(list(_STRIP_TAGS)):
        tag.decompose()

    def _is_chrome(elem) -> bool:
        if not isinstance(elem, bs4.Tag):
            return False
        return bool(_DISALLOWED_CLASSES.intersection(elem.get("class", [])))

    for tag in soup.find_all(_is_chrome):
        tag.decompose()

    return soup


# Elements a browser lays out as blocks: each starts a new line of extracted
# text, while other elements' text stays in the line around it.
_BLOCK_TAGS = frozenset(
    "address article aside blockquote body caption center dd details dialog dir div dl dt"
    " fieldset figcaption figure footer form h1 h2 h3 h4 h5 h6 head header hgroup hr html"
    " legend li listing main menu nav ol optgroup option p plaintext pre search section"
    " summary table tbody td textarea tfoot th thead title tr ul xmp".split()
)
# Whitespace inside these elements is content, so their text is kept as written.
_PREFORMATTED_TAGS = frozenset(("listing", "plaintext", "pre", "textarea", "xmp"))
# Inline boxes whose text never runs into the text next to them.
_BOX_TAGS = frozenset(("button", "select"))
# Only these characters are whitespace in HTML. A no-break space is content.
_HTML_WHITESPACE = re.compile(r"[ \t\n\r\f]+")

# Text-bearing node types whose content is real page text. Script, style,
# template and comment strings are subclasses and are deliberately excluded.
_TEXT_NODES = (bs4.NavigableString, bs4.CData)


def _block_lines(soup: BeautifulSoup) -> List[str]:
    """Return the soup's text as one line per block element."""
    lines: List[str] = []
    buffer: List[str] = []

    def flush() -> None:
        joined = _HTML_WHITESPACE.sub(" ", "".join(buffer)).strip(" ")
        buffer.clear()
        if joined.strip():
            lines.append(joined)

    # An explicit stack, so deeply nested markup cannot exhaust the recursion limit.
    stack: List[tuple] = [(soup, "visit")]
    while stack:
        node, action = stack.pop()
        if action == "flush":
            flush()
        elif action == "gap":
            buffer.append(" ")
        elif isinstance(node, bs4.Tag):
            if node.name == "br":
                flush()
            elif node.name in _PREFORMATTED_TAGS:
                flush()
                preserved = node.get_text().strip("\n")
                if preserved.strip():
                    lines.append(preserved)
            else:
                if node.name in _BLOCK_TAGS:
                    flush()
                    stack.append((node, "flush"))
                elif node.name in _BOX_TAGS:
                    buffer.append(" ")
                    stack.append((node, "gap"))
                stack.extend((child, "visit") for child in reversed(node.contents))
        elif type(node) in _TEXT_NODES:
            buffer.append(str(node))
    flush()
    return lines


def get_text_from_soup(soup: BeautifulSoup) -> str:
    """Extract readable text from the soup with block-aware line joins."""
    if soup is None:
        return ""
    text = "\n".join(_block_lines(soup))
    return re.sub(r"\s{2,}", " ", text)
