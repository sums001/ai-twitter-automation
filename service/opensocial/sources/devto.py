"""Dev.to source via the public Forem API (no key required for reads).

    devto:
      tag: javascript        # optional; omit for the latest across all tags
      limit: 30
      full_text: true        # fetch each article's body_markdown (N requests)
"""

from __future__ import annotations

import asyncio

import httpx

from opensocial.core.models import ContentItem
from opensocial.sources.base import (
    DEFAULT_TIMEOUT,
    USER_AGENT,
    Setting,
    Source,
    parse_iso8601,
    register,
)

LIST_URL = "https://dev.to/api/articles"


@register
class DevToSource(Source):
    name = "devto"
    category = "tech"
    label = "dev.to"
    description = "Newest dev.to articles, optionally for one tag, with full article text."
    settings = (
        Setting("tag", "dev.to tag; empty returns the latest across all tags.", ""),
        Setting("limit", "Max items per fetch.", 30),
        Setting("full_text", "Fetch each article's full body (one request each).", True),
    )

    async def fetch(self) -> list[ContentItem]:
        limit: int = int(self.config.get("limit", 30))
        full_text: bool = bool(self.config.get("full_text", True))
        params = {"per_page": limit}
        if self.config.get("tag"):
            params["tag"] = self.config["tag"]

        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT}
        ) as client:
            resp = await client.get(LIST_URL, params=params)
            resp.raise_for_status()
            articles = resp.json()

            bodies: list[str | None]
            if full_text:
                sem = asyncio.Semaphore(5)

                async def detail(article) -> str | None:
                    async with sem:
                        r = await client.get(f"{LIST_URL}/{article['id']}")
                    if r.status_code == 200:
                        return r.json().get("body_markdown")
                    return None

                bodies = await asyncio.gather(*(detail(a) for a in articles))
            else:
                bodies = [None] * len(articles)

        items: list[ContentItem] = []
        for article, body in zip(articles, bodies):
            url = article.get("url")
            title = article.get("title")
            if not url or not title:
                continue
            cover = article.get("cover_image") or article.get("social_image")
            user = article.get("user") or {}
            items.append(
                ContentItem(
                    source_name=self.name,
                    source_category=self.category,
                    title=title,
                    url=url,
                    summary=article.get("description"),
                    body=body,
                    author=user.get("name"),
                    published_at=parse_iso8601(article.get("published_at")),
                    media_urls=[cover] if cover else [],
                    tags=article.get("tag_list", []) or [],
                    engagement={
                        "reactions": article.get("public_reactions_count"),
                        "comments": article.get("comments_count"),
                        "reading_time": article.get("reading_time_minutes"),
                    },
                    raw_metadata=article,
                )
            )
        return items
