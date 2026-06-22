"""Image providers behind one tiny interface.

``ImageProvider.image_for(prompt)`` returns an :class:`ImageResult` (or
``None`` for the no-image provider). Phase 3 only needs the image *reference*
(a URL + attribution); actually downloading bytes for media upload is a Phase 4
publish concern.

AI image *generation* (Pollinations/DALL·E) was removed — Pollinations moved to a
paid, rate-limited model and DALL·E needs paid API access — so images now come
only from real sources:

* :class:`UnsplashProvider` — real stock photos via the Unsplash API (needs
  ``UNSPLASH_ACCESS_KEY``); runs one relevance-ordered search and takes the top
  hit, degrading to no image when the key is absent, a request fails, or nothing
  matches.
* :class:`SourceMediaProvider` — a marker provider for the "Content" image
  source: it never fetches; :func:`generate._attach_image` uses the source
  item's own media instead.
* :class:`NoneProvider` — supplies no image (for ``never`` visual rules, when a
  niche disables images, or by default).

``get_image_provider(config)`` picks one from the **per-niche** ``image_source``
field (``unsplash`` | ``content`` | ``none``; default ``none``). For Unsplash the
workspace's own ``UNSPLASH_ACCESS_KEY`` is read from ``config['unsplash_access_key']``
(injected by the generation command), falling back to the process env.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ImageResult:
    url: str
    provider: str
    attribution: str | None = None
    path: str | None = None  # filled at publish time when bytes are downloaded


class ImageProvider(ABC):
    name: str

    @abstractmethod
    def image_for(self, prompt: str) -> ImageResult | None:
        raise NotImplementedError


class UnsplashProvider(ImageProvider):
    """Real stock photos via the Unsplash API. Needs ``UNSPLASH_ACCESS_KEY``.

    The URL only exists after a real API call, so this hits the network. It
    degrades gracefully: with no key (or any request error) it returns ``None``
    rather than failing the generation run.

    It issues **one** query (the caller builds a tight, on-topic one — ideally an
    LLM-written visual phrase) against ``/search/photos`` ordered by relevance and
    takes the single top hit. We deliberately don't broaden the query or pick a
    random match: a vague fallback returns generic, off-topic stock, which is what
    "many random images" was. One good query → the most relevant photo, or none.
    """

    name = "unsplash"
    _API = "https://api.unsplash.com/search/photos"

    def __init__(
        self, width: int = 1024, height: int = 1024, access_key: str | None = None
    ) -> None:
        # Unsplash serves its own sizes; width/height are kept for interface
        # parity (and a squarish crop hint) but don't force exact dimensions.
        self.width = int(width)
        self.height = int(height)
        # The workspace's own Unsplash key, injected by the caller. Falls back to
        # the process env so an operator-set ``UNSPLASH_ACCESS_KEY`` still works.
        self.access_key = (access_key or "").strip() or None

    def image_for(self, prompt: str) -> ImageResult | None:
        text = (prompt or "").strip()
        if not text:
            return None
        import os

        key = self.access_key or os.environ.get("UNSPLASH_ACCESS_KEY")
        if not key:
            return None

        # One query, the most relevant result — no broadening, no random pick.
        return self._fetch(text, key)

    def _fetch(self, query: str, key: str) -> ImageResult | None:
        try:
            import json as _json
            from urllib.parse import urlencode
            from urllib.request import Request, urlopen

            orientation = "squarish" if self.width == self.height else (
                "landscape" if self.width > self.height else "portrait"
            )
            params = urlencode(
                {
                    "query": query,
                    "orientation": orientation,
                    "content_filter": "high",
                    "order_by": "relevant",
                    "per_page": 1,
                }
            )
            req = Request(
                f"{self._API}?{params}",
                headers={"Authorization": f"Client-ID {key}", "Accept-Version": "v1"},
            )
            with urlopen(req, timeout=10) as resp:  # noqa: S310 (trusted host)
                data = _json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

        results = data.get("results") or []
        if not results:
            return None
        top = results[0]
        url = (top.get("urls") or {}).get("regular")
        if not url:
            return None
        author = (top.get("user") or {}).get("name")
        attribution = f"Photo by {author} on Unsplash" if author else "Unsplash"
        return ImageResult(url=url, provider=self.name, attribution=attribution)


class SourceMediaProvider(ImageProvider):
    """Marker for the "Content" image source — use the source item's own media.

    It never fetches an image itself; :func:`generate._attach_image` keys off
    ``name == "content"`` to attach the fetched item's media instead.
    """

    name = "content"

    def image_for(self, prompt: str) -> ImageResult | None:
        return None


class NoneProvider(ImageProvider):
    name = "none"

    def image_for(self, prompt: str) -> ImageResult | None:
        return None


def get_image_provider(config: dict | None) -> ImageProvider:
    """Build the image provider for a niche from its ``image_source`` field.

    * ``unsplash`` — stock photos (:class:`UnsplashProvider`).
    * ``content`` — use the fetched item's own media (:class:`SourceMediaProvider`).
    * ``none`` (default) — no images. Legacy ``ai`` also maps here, since AI
      image generation was removed.
    """
    cfg = config or {}
    source = (cfg.get("image_source") or "none").strip().lower()
    if source == "unsplash":
        return UnsplashProvider(access_key=cfg.get("unsplash_access_key"))
    if source == "content":
        return SourceMediaProvider()
    return NoneProvider()
