"""Medium source — RSS feeds by tag (or explicit feed URLs).

    medium:
      category: tech
      tags:
        - artificial-intelligence
        - cryptocurrency
"""

from __future__ import annotations

from opensocial.core.models import ContentItem
from opensocial.sources.base import Setting, Source, register
from opensocial.sources.rss import fetch_feeds


@register
class MediumSource(Source):
    name = "medium"
    category = "generic"
    label = "Medium"
    description = "Newest Medium posts for each tag (about 10 per tag), via tag RSS feeds."
    settings = (
        Setting("tags", "Medium tags, e.g. artificial-intelligence.", []),
        Setting("feeds", "Extra Medium feed URLs (publications, authors).", []),
        Setting("category", "Category tag for fetched items.", "generic"),
    )

    async def fetch(self) -> list[ContentItem]:
        category: str = self.config.get("category", self.category)
        feeds: list[str] = list(self.config.get("feeds", []) or [])
        feeds += [
            f"https://medium.com/feed/tag/{tag}"
            for tag in (self.config.get("tags", []) or [])
        ]
        return await fetch_feeds(feeds, self.name, category)
