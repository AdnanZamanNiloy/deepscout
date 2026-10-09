from __future__ import annotations

"""SearchClient cache methods, split into a mixin (refactor).

Moved verbatim from `app/agents/search.py`; `SearchClient` inherits this so
behaviour and the class surface are unchanged."""

from typing import Any, Dict, List

from app.agents.sources import is_primary_source
from app.agents.searchkit.identity import source_identity
from app.agents.searchkit.types import SearchResult

class CacheMixin:
        @classmethod
        def _decode_cached(cls, cached: Any) -> List[SearchResult]:
            """Cache entries are plain dicts (portable across processes and
            pickle-safe). Entries written by an older build stored SearchResult
            objects directly, so both shapes are accepted for one TTL."""
            if not isinstance(cached, list):
                return []
            out: List[SearchResult] = []
            for row in cached:
                if isinstance(row, SearchResult):
                    out.append(row)
                elif isinstance(row, dict):
                    out.append(cls._from_cache(row))
            return out

        @staticmethod
        def _to_cache(result: SearchResult) -> Dict[str, Any]:
            payload = result.to_dict()
            payload["is_content_fetched"] = result.is_content_fetched
            payload["reliability_score"] = result.reliability_score
            return payload

        @staticmethod
        def _from_cache(row: Dict[str, Any]) -> SearchResult:
            # Identity is recomputed from the URL rather than trusted from the
            # cache row: an entry written before these fields existed (or by an
            # older build) must still come back with a usable source_domain,
            # otherwise a cache hit would render an empty source label where a
            # fresh search showed the domain.
            domain, _ = source_identity(str(row.get("url", "")))
            result = SearchResult(
                title=str(row.get("title", "")),
                url=str(row.get("url", "")),
                snippet=str(row.get("snippet", "")),
                content=str(row.get("content", "")),
                provider=str(row.get("provider", "cache")),
                search_type=str(row.get("search_type", "general")),
                published_at=str(row.get("published_at", "")),
                matched_query=str(row.get("matched_query", "")),
                source_domain=domain,
                publisher=str(row.get("publisher", "") or ""),
                retrieval_provider=str(row.get("retrieval_provider", "") or ""),
                retrieval_engine=str(row.get("retrieval_engine", "") or ""),
                retrieval_engines=[
                    str(e) for e in (row.get("retrieval_engines") or []) if str(e)
                ],
            )
            result.reliability_score = float(row.get("reliability_score", 0.0) or 0.0)
            result.content_length = int(row.get("content_length", 0) or 0)
            result.is_content_fetched = bool(row.get("is_content_fetched", bool(result.content)))
            result.is_primary = bool(row.get("is_primary", is_primary_source(result.url)))
            return result
