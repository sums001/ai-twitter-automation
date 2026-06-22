"""Per-niche image source — the General-tab Image source dropdown
(``image_source``) selects Unsplash / Content / None. AI image generation was
removed, so there is no AI/Pollinations/DALL·E path."""

from __future__ import annotations

from opensocial.ai.images import (
    NoneProvider,
    SourceMediaProvider,
    UnsplashProvider,
    get_image_provider,
)
from opensocial.core.generate import _attach_image


def _cfg(source):
    cfg = {"slug": "tech"}
    if source is not None:
        cfg["image_source"] = source
    return cfg


def test_source_none_disables_images():
    assert isinstance(get_image_provider(_cfg("none")), NoneProvider)


def test_source_content_uses_source_media_provider():
    assert isinstance(get_image_provider(_cfg("content")), SourceMediaProvider)


def test_source_unsplash():
    assert isinstance(get_image_provider(_cfg("unsplash")), UnsplashProvider)


def test_default_when_image_source_unset_is_none():
    # AI generation removed → the default (and legacy "ai") is no image.
    assert isinstance(get_image_provider(_cfg(None)), NoneProvider)
    assert isinstance(get_image_provider(_cfg("ai")), NoneProvider)


def test_content_provider_attaches_item_media():
    provider = SourceMediaProvider()
    # Content image source uses the item's own media…
    url, attr, prov = _attach_image(
        provider, niche_name="Tech", subject="x",
        item_media=["https://img/1.png"],
    )
    assert (url, attr, prov) == ("https://img/1.png", "source", "source")
    # …and yields no image when the item has none.
    assert _attach_image(
        provider, niche_name="Tech", subject="x", item_media=[],
    ) == (None, None, None)


def test_none_provider_yields_no_image():
    assert _attach_image(
        NoneProvider(), niche_name="Tech", subject="x", item_media=None,
    ) == (None, None, None)


def test_unsplash_degrades_to_none_without_key(monkeypatch):
    monkeypatch.delenv("UNSPLASH_ACCESS_KEY", raising=False)
    assert UnsplashProvider().image_for("a sunset over mountains") is None


def test_unsplash_uses_per_workspace_key_over_env(monkeypatch):
    # The per-workspace key (injected via config) wins, and works even when the
    # process env has none set — so two workspaces can use different keys.
    monkeypatch.delenv("UNSPLASH_ACCESS_KEY", raising=False)
    provider = get_image_provider({"image_source": "unsplash", "unsplash_access_key": "ws-key"})
    assert isinstance(provider, UnsplashProvider)
    assert provider.access_key == "ws-key"

    seen: dict[str, str] = {}

    def fake_fetch(self, query, key):
        seen["key"] = key
        return None

    monkeypatch.setattr(UnsplashProvider, "_fetch", fake_fetch)
    provider.image_for("a sunset over mountains")
    assert seen["key"] == "ws-key"


def test_unsplash_issues_one_query_without_broadening(monkeypatch):
    # The query is sent verbatim, exactly once — no progressively-broader
    # fallbacks that would degrade to generic, off-topic stock.
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "k")

    tried: list[str] = []

    def fake_fetch(self, query, key):
        tried.append(query)
        return None  # a no-match must NOT trigger a broadened retry

    monkeypatch.setattr(UnsplashProvider, "_fetch", fake_fetch)
    assert UnsplashProvider().image_for("openai office building") is None
    assert tried == ["openai office building"]


def test_unsplash_takes_top_relevance_search_result(monkeypatch):
    # _fetch parses the /search/photos response and returns its top hit.
    monkeypatch.setenv("UNSPLASH_ACCESS_KEY", "k")
    import json
    import io

    captured: dict[str, str] = {}

    def fake_urlopen(req, timeout=10):
        captured["url"] = req.full_url
        payload = {
            "results": [
                {"urls": {"regular": "https://img/top.jpg"}, "user": {"name": "Ada"}},
                {"urls": {"regular": "https://img/second.jpg"}},
            ]
        }
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    res = UnsplashProvider().image_for("city skyline")
    assert res is not None and res.url == "https://img/top.jpg"
    assert res.attribution == "Photo by Ada on Unsplash"
    assert "search/photos" in captured["url"] and "order_by=relevant" in captured["url"]


class _RecordingUnsplash(UnsplashProvider):
    """Captures the query string instead of hitting the network."""

    def image_for(self, prompt):
        self.seen = prompt
        return None


def test_attach_image_feeds_unsplash_the_clean_query():
    prov = _RecordingUnsplash()
    _attach_image(
        prov, niche_name="Crypto",
        subject="BlackRock files to list its bitcoin income ETF", item_media=None,
    )
    # Routed through unsplash_query — a tight niche anchor + the most salient
    # subject word, not the raw headline or an AI-illustration prompt.
    assert "illustration" not in prov.seen
    assert prov.seen == "Crypto BlackRock"
