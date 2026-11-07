"""The Guardian source via the Guardian Content API (free key required).

    guardian:
      query: technology       # optional free-text search
      section: technology     # optional section filter
      limit: 20
      # api_key or env GUARDIAN_API_KEY
"""

from __future__ import annotations

import httpx

from opensocial.core.models import ContentItem
from opensocial.sources.base import (
    DEFAULT_TIMEOUT,
    AUTH_REQUIRED,
    USER_AGENT,
    Setting,
    Source,
    parse_iso8601,
    register,
    resolve_api_key,
)

API_URL = "https://content.guardianapis.com/search"


@register
class GuardianSource(Source):
    name = "guardian"
    category = "news"
    label = "The Guardian"
    description = "Newest Guardian articles by search or section, with full article text."
    auth = AUTH_REQUIRED
    key_envs = ("GUARDIAN_API_KEY",)
    key_help = "A free developer key from open-platform.theguardian.com (about 500 calls/day)."
    settings = (
        Setting("query", "Free-text search.", ""),
        Setting("section", "Section filter, e.g. technology, politics.", ""),
        Setting("limit", "Max items per fetch.", 20),
    )

    async def fetch(self) -> list[ContentItem]:
        key = resolve_api_key(self.config, "GUARDIAN_API_KEY", source_name=self.name)
        limit = int(self.config.get("limit", 20))

        params = {
            "api-key": key,
            "show-fields": "trailText,bodyText,thumbnail,byline",
            "page-size": limit,
            "order-by": "newest",
        }
        if self.config.get("query"):
            params["q"] = self.config["query"]
        if self.config.get("section"):
            params["section"] = self.config["section"]

        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT}
        ) as client:
            resp = await client.get(API_URL, params=params)
            resp.raise_for_status()
            data = resp.json()

        items: list[ContentItem] = []
        for res in data.get("response", {}).get("results", []):
            fields = res.get("fields", {}) or {}
            thumb = fields.get("thumbnail")
            section = res.get("sectionName")
            items.append(
                ContentItem(
                    source_name=self.name,
                    source_category=self.category,
                    title=res.get("webTitle", ""),
                    url=res.get("webUrl", ""),
                    summary=fields.get("trailText"),
                    body=fields.get("bodyText"),
                    author=fields.get("byline"),
                    published_at=parse_iso8601(res.get("webPublicationDate")),
                    media_urls=[thumb] if thumb else [],
                    tags=[section] if section else [],
                    raw_metadata=res,
                )
            )
        return items
