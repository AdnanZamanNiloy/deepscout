"""HTML text extraction.

Cleans a parsed page (removing scripts, styles, nav chrome), then walks the
document into one line per block element so adjacent inline markup (a link, an
emphasis) does not split the sentence — and the word — it sat in. Also extracts
a title and a scored list of candidate images.
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


def get_relevant_images(soup: BeautifulSoup, url: str) -> List[dict]:
    """Extract candidate images (url + relevance score), highest score first."""
    image_urls: List[dict] = []
    try:
        if soup is None:
            return []
        all_images = soup.find_all("img", src=True)
        for img in all_images:
            img_src = urljoin(url, img["src"])
            if not img_src.startswith(("http://", "https://")):
                continue
            score = 0
            classes = img.get("class") or []
            if isinstance(classes, str):
                classes = classes.split()
            elif not isinstance(classes, (list, tuple, set)):
                classes = []
            if any(
                cls in classes
                for cls in ["header", "featured", "hero", "thumbnail", "main", "content"]
            ):
                score = 4
            elif img.get("width") and img.get("height"):
                width = parse_dimension(img["width"])
                height = parse_dimension(img["height"])
                if width and height:
                    if width >= 2000 and height >= 1000:
                        score = 3
                    elif width >= 1600 or height >= 800:
                        score = 2
                    elif width >= 800 or height >= 500:
                        score = 1
                    elif width >= 500 or height >= 300:
                        score = 0
                    else:
                        continue
            image_urls.append({"url": img_src, "score": score})
        return sorted(image_urls, key=lambda x: x["score"], reverse=True)[:10]
    except Exception as e:
        logger.error("Error in get_relevant_images: %s", e)
        return []


def parse_dimension(value) -> Optional[int]:
    """Parse an HTML width/height value (handling plain numbers and 'px')."""
    if value is None:
        return None
    if not isinstance(value, str):
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return None
    if value.lower().endswith("px"):
        value = value[:-2]
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def extract_title(soup: BeautifulSoup) -> str:
    """Extract the page title as plain text (``""`` when absent)."""
    title_tag = soup.title
    if not title_tag:
        return ""
    return title_tag.get_text(strip=True)


def get_image_hash(image_url: str) -> Optional[str]:
    """Hash of an image's filename + essential query params, for dedup."""
    try:
        parsed_url = urlparse(image_url)
        filename = parsed_url.path.split("/")[-1]
        query_params = parse_qs(parsed_url.query)
        essential_params = query_params.get("url", [])
        image_identifier = filename + "".join(essential_params)
        return hashlib.md5(image_identifier.encode()).hexdigest()
    except Exception as e:
        logger.error("Error calculating image hash for %s: %s", image_url, e)
        return None


def clean_soup(soup: BeautifulSoup) -> BeautifulSoup:
    """Remove scripts, styles and navigation chrome from the soup."""
    for tag in soup.find_all(
        ["script", "style", "footer", "header", "nav", "menu", "sidebar", "svg"]
    ):
        tag.decompose()

    disallowed_class_set = {"nav", "menu", "sidebar", "footer"}

    def does_tag_have_disallowed_class(elem) -> bool:
        if not isinstance(elem, bs4.Tag):
            return False
        return any(
            cls_name in disallowed_class_set for cls_name in elem.get("class", [])
        )

    for tag in soup.find_all(does_tag_have_disallowed_class):
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


def _block_lines(soup: BeautifulSoup) -> List[str]:
    """Return the soup's text as one line per block element."""
    lines: List[str] = []
    line: List[str] = []

    def end_line() -> None:
        text = _HTML_WHITESPACE.sub(" ", "".join(line)).strip(" ")
        line.clear()
        if text.strip():
            lines.append(text)

    # An explicit stack, so deeply nested markup cannot exhaust the recursion limit.
    stack: List[tuple] = [(soup, "visit")]
    while stack:
        node, action = stack.pop()
        if action == "end_line":
            end_line()
        elif action == "space":
            line.append(" ")
        elif isinstance(node, bs4.Tag):
            if node.name == "br":
                end_line()
            elif node.name in _PREFORMATTED_TAGS:
                end_line()
                text = node.get_text().strip("\n")
                if text.strip():
                    lines.append(text)
            else:
                if node.name in _BLOCK_TAGS:
                    end_line()
                    stack.append((node, "end_line"))
                elif node.name in _BOX_TAGS:
                    line.append(" ")
                    stack.append((node, "space"))
                stack.extend((child, "visit") for child in reversed(node.contents))
        # Script, style, template and comment strings are subclasses and are not
        # page text, the same filter get_text() applies by default.
        elif type(node) in (bs4.NavigableString, bs4.CData):
            line.append(str(node))
    end_line()
    return lines


def get_text_from_soup(soup: BeautifulSoup) -> str:
    """Extract readable text from the soup with block-aware line joins."""
    if soup is None:
        return ""
    text = "\n".join(_block_lines(soup))
    return re.sub(r"\s{2,}", " ", text)
