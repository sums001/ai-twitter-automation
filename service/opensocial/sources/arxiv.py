"""ArXiv source via the public ArXiv API (Atom; no key required).

    arxiv:
      query: "cat:cs.AI OR cat:cs.LG"
      limit: 30
"""

from __future__ import annotations

from datetime import datetime, timezone

import feedparser
import httpx

from opensocial.core.models import ContentItem
from opensocial.sources.base import (
    DEFAULT_TIMEOUT,
    USER_AGENT,
    Setting,
    Source,
    register,
)
from opensocial.sources.rss import struct_to_dt

API_URL = "https://export.arxiv.org/api/query"


@register
class ArxivSource(Source):
    name = "arxiv"
    category = "science"
    label = "arXiv"
    description = "Newest arXiv research papers for a search query (title and abstract)."
    settings = (
        Setting("query", "arXiv search, e.g. cat:cs.AI OR cat:cs.LG.", "all:artificial intelligence"),
        Setting("limit", "Max items per fetch.", 30),
    )

    async def fetch(self) -> list[ContentItem]:
        query: str = self.config.get("query") or self.config.get(
            "search_query", "all:artificial intelligence"
        )
        limit: int = int(self.config.get("limit", 30))

        params = {
            "search_query": query,
            "start": 0,
            "max_results": limit,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            resp = await client.get(API_URL, params=params)
            resp.raise_for_status()
            parsed = feedparser.parse(resp.content)

        items: list[ContentItem] = []
        for entry in parsed.entries:
            url = entry.get("link")
            title = entry.get("title")
            if not url or not title:
                continue
            abstract = entry.get("summary")
            authors = [a.get("name") for a in entry.get("authors", []) if a.get("name")]
            items.append(
                ContentItem(
                    source_name=self.name,
                    source_category=self.category,
                    title=" ".join(title.split()),  # arxiv wraps titles
                    url=url,
                    body=abstract,
                    summary=abstract,
                    author=", ".join(authors) or None,
                    published_at=struct_to_dt(entry.get("published_parsed"))
                    or datetime.now(timezone.utc),
                    tags=[t.get("term") for t in entry.get("tags", []) if t.get("term")],
                    raw_metadata=dict(entry),
                )
            )
        return items
