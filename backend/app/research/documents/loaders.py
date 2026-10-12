"""Load local or online documents into page records.

Used only by the ``local`` and ``hybrid`` source modes; the default ``web`` mode
never touches these. Local files are walked by extension; online documents are
downloaded to a temp file and loaded the same way. Both return the pipeline's
``{raw_content, url}`` page shape.

Supported formats are the text-bearing ones the app's dependencies can read:
PDF (PyMuPDF), plain text, markdown and HTML. Office and archive formats are
intentionally not supported: they are not reachable from a web run and nothing
in the pipeline consumes them.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from typing import List, Union
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.research.scraping.extract import clean_soup, get_text_from_soup
from app.research.url_security import UnsafeURLError, validate_url


class DocumentLoader:
    """Load documents from a local path or a list of file paths."""

    def __init__(self, path: Union[str, List[str]]):
        self.path = path

    async def load(self) -> List[dict]:
        files = await asyncio.to_thread(self._collect_files)
        tasks = [self._load_document(path, ext) for path, ext in files]
        docs: List[dict] = []
        for pages in await asyncio.gather(*tasks):
            docs.extend(pages)
        if not docs:
            raise ValueError("Failed to load any documents!")
        return docs

    def _collect_files(self) -> List[tuple]:
        """Walk the configured path(s) and return ``(file_path, extension)`` pairs."""
        files: List[tuple] = []
        if isinstance(self.path, list):
            for file_path in self.path:
                if os.path.isfile(file_path):
                    files.append((file_path, self._extension(file_path)))
        elif isinstance(self.path, (str, bytes, os.PathLike)):
            for root, _dirs, names in os.walk(self.path):
                for file in names:
                    file_path = os.path.join(root, file)
                    files.append((file_path, self._extension(file)))
        else:
            raise ValueError("Invalid type for path.")
        return files

    async def _load_document(self, file_path: str, file_extension: str) -> List[dict]:
        try:
            return await asyncio.to_thread(self._read_file, file_path, file_extension)
        except Exception as e:
            print(f"Failed to load document: {file_path}: {e}")
            return []

    @staticmethod
    def _read_file(file_path: str, file_extension: str) -> List[dict]:
        name = os.path.basename(file_path)
        if file_extension == "pdf":
            import fitz

            with fitz.open(file_path) as doc:
                text = "\n".join(page.get_text() for page in doc)
        elif file_extension in ("html", "htm"):
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                soup = clean_soup(BeautifulSoup(fh.read(), "lxml"))
            text = get_text_from_soup(soup)
        elif file_extension in ("md", "txt", ""):
            with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        else:
            return []
        if not text.strip():
            return []
        return [{"raw_content": text, "url": name}]

    @staticmethod
    def _extension(path: str) -> str:
        return os.path.splitext(path.split("?")[0])[1].strip(".").lower()


class OnlineDocumentLoader:
    """Download and load documents from a list of URLs."""

    def __init__(self, urls: List[str]):
        self.urls = urls

    async def load(self) -> List[dict]:
        docs: List[dict] = []
        for url in self.urls:
            docs.extend(await self._download_and_process(url))
        if not docs:
            raise ValueError("Failed to load any documents!")
        return docs

    async def _download_and_process(self, url: str) -> List[dict]:
        try:
            validate_url(url)
        except UnsafeURLError as e:
            print(f"Skipping unsafe document URL {url}: {e}")
            return []

        suffix = os.path.splitext(urlparse(url).path)[1].lower()
        try:
            async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
                response = await client.get(url, timeout=6.0, follow_redirects=True)
                if response.status_code != 200:
                    print(f"Failed to download {url}: HTTP {response.status_code}")
                    return []
                content = response.content

            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                pages = await asyncio.to_thread(
                    DocumentLoader._read_file, tmp_path, suffix.strip(".").lower()
                )
            finally:
                os.remove(tmp_path)
            # Tag each page with its origin URL rather than the temp filename.
            for page in pages:
                page["url"] = url
            return pages
        except Exception as e:
            print(f"Failed to process {url}: {e}")
            return []
