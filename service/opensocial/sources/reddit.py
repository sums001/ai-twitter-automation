"""Reddit source via the public Atom feeds (no key required).

Reddit blocks unauthenticated access to the ``.json`` listing endpoints with a
403 anti-bot page, but the equivalent ``.rss`` (Atom) feeds remain open. We
fetch ``/r/<sub>/<sort>/.rss`` per subreddit and parse it with feedparser.

Tradeoff vs. the OAuth API: Atom feeds carry the title, link, author, timestamp
and post body, but **not** the score / comment counts, so ``engagement`` is
left empty (prioritization simply treats those signals as absent).

    reddit:
      subreddits: [cryptocurrency, bitcoin]
      sort: hot              # hot | new | top | rising
      limit: 25
      time: day             # only for sort=top: hour | day | week | month | year | all
      request_delay: 1.5    # seconds paused between subreddits (anti-burst)
      retries: 2            # retry attempts per host on 429 / 5xx
      backoff: 2.0          # base seconds for exponential backoff
      max_wait: 30          # cap on a single backoff/Retry-After sleep

Reddit rejects the default httpx/library User-Agent, so we send a browser-like
one. Unauthenticated reads are throttled hard from datacenter IPs — a 429 can
hit on the *first* request regardless of volume. To survive that we: retry with
exponential backoff honoring the ``Retry-After`` header, fall back from
``www`` to ``old.reddit.com``, and space requests apart. Each subreddit fails
independently, so a rate-limited one no longer aborts the others. If the wait
demanded exceeds ``max_wait`` we skip that subreddit rather than block the
pipeline. For a robust fix, run this source from a residential IP (VPS IPs are
the most heavily throttled).
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

import feedparser
import httpx

from opensocial.core.models import ContentItem
from opensocial.sources.base import DEFAULT_TIMEOUT, Source, register
from opensocial.sources.rss import struct_to_dt

_SORTS = {"hot", "new", "top", "rising"}
# www throttles unauthenticated reads aggressively; old.reddit.com is served by
# a different path and sometimes answers when www returns 429.
_HOSTS = ("https://www.reddit.com", "https://old.reddit.com")
_RETRY_STATUS = {429, 500, 502, 503, 504}
# Reddit serves an anti-bot block page to library User-Agents; a browser UA
# (plus the .rss path) is accepted for unauthenticated reads.
_REDDIT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.I)


def _retry_after(resp: httpx.Response) -> float | None:
    """Seconds from a numeric ``Retry-After`` header, or None if absent/HTTP-date."""
    val = resp.headers.get("Retry-After")
    if not val:
        return None
    try:
        return float(val)
    except ValueError:
        return None  # HTTP-date form — ignore and fall back to our own backoff


@register
class RedditSource(Source):
    name = "reddit"
    category = "social"

    async def fetch(self) -> list[ContentItem]:
        subreddits: list[str] = self.config.get("subreddits", []) or []
        sort: str = self.config.get("sort", "hot")
        if sort not in _SORTS:
            sort = "hot"
        limit: int = int(self.config.get("limit", 25))
        time_filter: str = self.config.get("time", "day")
        retries: int = int(self.config.get("retries", 2))
        backoff: float = float(self.config.get("backoff", 2.0))
        delay: float = float(self.config.get("request_delay", 1.5))
        max_wait: float = float(self.config.get("max_wait", 30.0))

        params: dict[str, str | int] = {"limit": limit}
        if sort == "top":
            params["t"] = time_filter

        items: list[ContentItem] = []
        errors: list[str] = []
        async with httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            headers={
                "User-Agent": _REDDIT_UA,
                "Accept": "application/atom+xml, application/xml, text/xml, */*",
                "Accept-Language": "en-US,en;q=0.9",
            },
            follow_redirects=True,
        ) as client:
            for i, name in enumerate(subreddits):
                if i:
                    await asyncio.sleep(delay)  # don't burst across subreddits
                try:
                    content = await self._fetch_feed(
                        client, name, sort, params, retries, backoff, max_wait
                    )
                except Exception as exc:
                    errors.append(f"r/{name}: {exc}")
                    continue
                if content is None:
                    errors.append(f"r/{name}: rate-limited (skipped)")
                    continue
                parsed = feedparser.parse(content)
                for entry in parsed.entries:
                    item = self._to_item(entry, name)
                    if item is not None:
                        items.append(item)

        # Only surface a hard failure if every subreddit failed; otherwise a
        # partial result (some subs rate-limited) is still useful.
        if subreddits and not items and errors:
            raise RuntimeError("; ".join(errors))
        return items

    async def _fetch_feed(
        self,
        client: httpx.AsyncClient,
        name: str,
        sort: str,
        params: dict,
        retries: int,
        backoff: float,
        max_wait: float,
    ) -> bytes | None:
        """Fetch one subreddit's Atom feed with retry + host fallback.

        Returns the raw feed bytes, or None if every attempt was rate-limited
        within budget (caller treats None as a soft skip). Raises on a
        non-retryable HTTP error (e.g. 404) or an exhausted 5xx.
        """
        last_status: int | None = None
        for host in _HOSTS:
            url = f"{host}/r/{name}/{sort}/.rss"
            for attempt in range(retries + 1):
                resp = await client.get(url, params=params)
                if resp.status_code not in _RETRY_STATUS:
                    resp.raise_for_status()  # non-retryable error -> raise to caller
                    return resp.content
                last_status = resp.status_code
                if attempt == retries:
                    break  # exhausted on this host; try the next one
                wait = _retry_after(resp)
                if wait is None:
                    wait = backoff * (2**attempt)
                if wait > max_wait:
                    return None  # asked to wait too long — skip, don't block
                await asyncio.sleep(wait)
        if last_status == 429:
            return None  # soft skip: rate-limited everywhere we tried
        raise RuntimeError(f"reddit returned {last_status}")

    def _to_item(self, entry, subreddit: str) -> ContentItem | None:
        url = entry.get("link")
        title = entry.get("title")
        if not url or not title:
            return None

        body = None
        contents = entry.get("content")
        if contents:
            body = contents[0].get("value")

        media: list[str] = []
        if body:
            m = _IMG_RE.search(body)
            if m and m.group(1).startswith("http"):
                media.append(m.group(1))

        author = entry.get("author")  # e.g. "/u/someone"
        published = struct_to_dt(
            entry.get("published_parsed") or entry.get("updated_parsed")
        ) or datetime.now(timezone.utc)

        return ContentItem(
            source_name=self.name,
            source_category=self.category,
            title=title,
            url=url,
            body=body,
            author=author,
            published_at=published,
            media_urls=media,
            tags=[subreddit],
            engagement={},  # Atom feeds don't expose score / comment counts
            raw_metadata={
                "id": entry.get("id"),
                "subreddit": subreddit,
                "permalink": url,
            },
        )
