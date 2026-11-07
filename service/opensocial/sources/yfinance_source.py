"""Yahoo Finance source via the unofficial ``yfinance`` library (no key).

    yfinance:
      symbols: [AAPL, MSFT, BTC-USD]
      limit: 10

yfinance is synchronous, so the blocking call runs in a worker thread. Its
news payload shape has changed across versions; both the legacy flat shape
and the newer nested ``content`` shape are handled.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from opensocial.core.models import ContentItem
from opensocial.sources.base import Setting, Source, parse_iso8601, register


@register
class YFinanceSource(Source):
    name = "yfinance"
    category = "finance"
    label = "Yahoo Finance"
    description = (
        "Yahoo Finance news for the ticker symbols you list. Uses the unofficial "
        "yfinance library, so it can break if Yahoo changes its site."
    )
    settings = (
        Setting("symbols", "Ticker symbols, e.g. AAPL, BTC-USD.", []),
        Setting("limit", "Max news items per symbol.", 10),
    )

    async def fetch(self) -> list[ContentItem]:
        symbols: list[str] = self.config.get("symbols", []) or []
        limit: int = int(self.config.get("limit", 10))
        return await asyncio.to_thread(self._fetch_sync, symbols, limit)

    def _fetch_sync(self, symbols: list[str], limit: int) -> list[ContentItem]:
        import yfinance as yf

        items: list[ContentItem] = []
        for symbol in symbols:
            try:
                news = yf.Ticker(symbol).news or []
            except Exception:
                news = []
            for entry in news[:limit]:
                item = self._to_item(entry, symbol)
                if item is not None:
                    items.append(item)
        return items

    def _to_item(self, entry: dict, symbol: str) -> ContentItem | None:
        # Newer yfinance nests fields under "content"; older is flat.
        content = entry.get("content", entry)

        title = content.get("title")
        url = (
            (content.get("canonicalUrl") or {}).get("url")
            or (content.get("clickThroughUrl") or {}).get("url")
            or entry.get("link")
        )
        if not title or not url:
            return None

        # Published time: ISO string (new) or unix seconds (old).
        pub_date = content.get("pubDate") or content.get("displayTime")
        if pub_date:
            published = parse_iso8601(pub_date)
        elif entry.get("providerPublishTime"):
            published = datetime.fromtimestamp(
                entry["providerPublishTime"], tz=timezone.utc
            )
        else:
            published = datetime.now(timezone.utc)

        provider = content.get("provider") or {}
        thumb = ((content.get("thumbnail") or {}).get("resolutions") or [{}])
        media = [thumb[0]["url"]] if thumb and thumb[0].get("url") else []

        return ContentItem(
            source_name=self.name,
            source_category=self.category,
            title=title,
            url=url,
            summary=content.get("summary") or content.get("description"),
            author=provider.get("displayName") or entry.get("publisher"),
            published_at=published,
            media_urls=media,
            tags=[symbol],
            raw_metadata=entry,
        )
