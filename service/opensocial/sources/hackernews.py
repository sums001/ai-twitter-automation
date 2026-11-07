"""Hacker News source via the Algolia HN Search API (no key required).

    hackernews:
      query: crypto      # empty string returns the latest front-page stories
      limit: 30
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from opensocial.core.models import ContentItem
from opensocial.sources.base import (
    DEFAULT_TIMEOUT,
    USER_AGENT,
    Setting,
    Source,
    register,
)

SEARCH_URL = "https://hn.algolia.com/api/v1/search_by_date"


@register
class HackerNewsSource(Source):
    name = "hackernews"
    category = "tech"
    trust = 0.65
    label = "Hacker News"
    description = "Newest Hacker News stories matching a query, via the Algolia HN API."
    settings = (
        Setting("query", "Search terms; empty returns the latest stories.", ""),
        Setting("limit", "Max items per fetch.", 30),
    )

    async def fetch(self) -> list[ContentItem]:
        query: str = self.config.get("query", "")
        limit: int = int(self.config.get("limit", 30))

        params = {"query": query, "tags": "story", "hitsPerPage": limit}
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT}
        ) as client:
            resp = await client.get(SEARCH_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        items: list[ContentItem] = []
        for hit in data.get("hits", []):
            object_id = hit.get("objectID")
            hn_url = f"https://news.ycombinator.com/item?id={object_id}"
            url = hit.get("url") or hn_url
            title = hit.get("title") or hit.get("story_title")
            if not title:
                continue

            published = datetime.fromtimestamp(
                hit.get("created_at_i", 0), tz=timezone.utc
            )

            items.append(
                ContentItem(
                    source_name=self.name,
                    source_category=self.category,
                    title=title,
                    url=url,
                    summary=hit.get("story_text") or None,
                    author=hit.get("author"),
                    published_at=published,
                    tags=hit.get("_tags", []) or [],
                    engagement={
                        "score": hit.get("points"),
                        "comments": hit.get("num_comments"),
                        "discussion_url": hn_url,
                    },
                    raw_metadata=hit,
                )
            )
        return items
