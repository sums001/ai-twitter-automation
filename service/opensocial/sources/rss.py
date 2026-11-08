"""Generic RSS/Atom source — reused across most feed-based sources.

Configure with a list of feed URLs and the category to tag items with:

    rss:
      category: crypto
      feeds:
        - https://decrypt.co/feed
        - https://www.coindesk.com/arc/outboundfeeds/rss/

The ``entry_to_item`` / ``fetch_feeds`` helpers are reused by other
feed-based plugins (e.g. Medium) so they share one parsing path.
"""

from __future__ import annotations

import calendar
from datetime import datetime, timezone
from urllib.parse import urlparse

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


def struct_to_dt(struct_time) -> datetime | None:
    if not struct_time:
        return None
    return datetime.fromtimestamp(calendar.timegm(struct_time), tz=timezone.utc)


def entry_media(entry) -> list[str]:
    urls: list[str] = []
    for media in getattr(entry, "media_content", []) or []:
        if media.get("url"):
            urls.append(media["url"])
    for enc in getattr(entry, "enclosures", []) or []:
        if enc.get("href"):
            urls.append(enc["href"])
    return urls


def entry_to_item(entry, source_name: str, category: str) -> ContentItem | None:
    url = entry.get("link")
    title = entry.get("title")
    if not url or not title:
        return None

    published = struct_to_dt(
        entry.get("published_parsed") or entry.get("updated_parsed")
    ) or datetime.now(timezone.utc)

    body = None
    contents = entry.get("content")
    if contents:
        body = contents[0].get("value")

    tags = [t.get("term") for t in entry.get("tags", []) if t.get("term")]

    return ContentItem(
        source_name=source_name,
        source_category=category,
        title=title,
        url=url,
        body=body,
        summary=entry.get("summary"),
        author=entry.get("author"),
        published_at=published,
        media_urls=entry_media(entry),
        tags=tags,
        raw_metadata=dict(entry),
    )


async def fetch_feeds(
    feeds: list[str], source_name: str, category: str
) -> list[ContentItem]:
    """Fetch and parse a list of RSS/Atom feeds into ``ContentItem``s."""
    items: list[ContentItem] = []
    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as client:
        for feed_url in feeds:
            try:
                resp = await client.get(feed_url)
                resp.raise_for_status()
            except httpx.HTTPError:
                continue
            parsed = feedparser.parse(resp.content)
            for entry in parsed.entries:
                item = entry_to_item(entry, source_name, category)
                if item is not None:
                    items.append(item)
    return items


@register
class RSSSource(Source):
    name = "rss"
    category = "generic"
    trust = 0.7
    label = "RSS feeds"
    description = "Newest entries from the RSS / Atom feeds you add (news sites, blogs)."
    settings = (
        Setting("feeds", "List of RSS / Atom feed URLs.", []),
        Setting("category", "Category tag for fetched items.", "generic"),
    )
    origin_key = "feeds"
    origin_label = "RSS / Atom feed URL"

    @classmethod
    def origin_display(cls, value: str) -> str:
        return urlparse(value).netloc or value

    async def fetch(self) -> list[ContentItem]:
        feeds: list[str] = self.config.get("feeds", []) or []
        category: str = self.config.get("category", self.category)
        return await fetch_feeds(feeds, self.name, category)
