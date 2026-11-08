"""Google News source — per-query search RSS (no key, no hard rate limit).

    googlenews:
      query: video games     # empty string returns the top-stories feed
      limit: 30
      # hl / gl / ceid: optional locale overrides (default en-US / US / US:en)

Google News exposes a search feed at ``/rss/search?q=...`` whose entries carry a
clean ``title``, ``link``, ``pubDate``, ``description`` and a ``<source>`` outlet
tag — exactly the shape the generic RSS plugin already parses, so this reuses
``fetch_feeds`` rather than writing a new parser.

Article ``link``s are Google redirect URLs; they're stored as-is. The pipeline
is link-free by design (posts never embed the source link by default) and the
redirect URL is stable per article, so it works fine as the de-duplication id.
"""

from __future__ import annotations

from urllib.parse import quote_plus

from opensocial.core.models import ContentItem
from opensocial.sources.base import Setting, Source, register

SEARCH_URL = "https://news.google.com/rss/search"
TOP_URL = "https://news.google.com/rss"


def build_feed_url(
    query: str, *, hl: str = "en-US", gl: str = "US", ceid: str = "US:en"
) -> str:
    """Compose the Google News RSS URL for a query (empty → top stories)."""
    locale = f"hl={quote_plus(hl)}&gl={quote_plus(gl)}&ceid={quote_plus(ceid)}"
    q = (query or "").strip()
    if not q:
        return f"{TOP_URL}?{locale}"
    return f"{SEARCH_URL}?q={quote_plus(q)}&{locale}"


@register
class GoogleNewsSource(Source):
    name = "googlenews"
    category = "news"
    trust = 0.75
    label = "Google News"
    description = (
        "Google News headlines for a search query, with the outlet name. "
        "Headline and short blurb only, no article text."
    )
    settings = (
        Setting("query", "Search terms; empty returns top stories.", ""),
        Setting("limit", "Max items per fetch.", 30),
        Setting("hl", "Interface language.", "en-US"),
        Setting("gl", "Country edition.", "US"),
        Setting("ceid", "Country:language edition id.", "US:en"),
    )

    async def fetch(self) -> list[ContentItem]:
        from opensocial.sources.rss import fetch_feeds

        query: str = self.config.get("query", "")
        limit: int = int(self.config.get("limit", 30))
        url = build_feed_url(
            query,
            hl=self.config.get("hl", "en-US"),
            gl=self.config.get("gl", "US"),
            ceid=self.config.get("ceid", "US:en"),
        )
        items = await fetch_feeds([url], self.name, self.category)
        return items[:limit] if limit > 0 else items
