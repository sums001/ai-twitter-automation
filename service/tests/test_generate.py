"""Drafting tests: text cleanup, tone choice, and the knowledge base -> draft
path, all with a mocked AI provider (no network, no model)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from opensocial.ai.images import NoneProvider
from opensocial.ai.text import TemplateProvider, TextProvider
from opensocial.core.db import (
    Base,
    GeneratedPost,
    make_engine,
    store_items,
)
from opensocial.core.config import NicheConfig
from opensocial.core.db import StoryUsage
from opensocial.core.filtering import filter_niche
from opensocial.core.generate import (
    choose_tone,
    draft_next,
    effective_length,
    fit_length,
    fix_cliches,
    html_to_text,
    polish,
    regenerate_text,
    resolve_samples,
    sanitize_formatting,
    strip_hashtags_and_emoji,
    strip_preamble,
    strip_wrapping_quotes,
    write_text,
)
from opensocial.core.knowledge import ingest
from opensocial.core.models import ContentItem
from opensocial.core.posttypes import ALL_TONES, PostTypesConfig, shuffled_deck

NICHE = "tech"


@pytest.fixture()
def session_factory(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def _item(title, **kw):
    now = datetime.now(timezone.utc)
    return ContentItem(
        source_name=kw.get("source_name", "hackernews"),
        source_category="tech",
        title=title,
        url=kw.get("url", f"https://example.com/{abs(hash(title))}"),
        body=kw.get("body", ""),
        summary=kw.get("summary", ""),
        published_at=kw.get("published", now),
        engagement=kw.get("engagement"),
        media_urls=kw.get("media_urls", []),
    )


class _FixedProvider(TextProvider):
    """Returns a canned string regardless of prompt (for post-processing tests)."""

    name = "fixed"

    def __init__(self, *texts):
        self._texts = list(texts)
        self.calls = []

    def generate(self, system, user):
        self.calls.append((system, user))
        return self._texts[min(len(self.calls), len(self._texts)) - 1]


_CONFIG = {
    "slug": "tech",
    "display_name": "Tech & Dev",
    "ai": {"text": {"provider": "template"}},
    "filters": {"relevance_keywords": []},
    "post_types": {
        "news": {"enabled": True},
        "spotlight": {"enabled": True},
        "take": {"enabled": True},
    },
}
NICHES = [NicheConfig.from_dict(_CONFIG)]


# --- unit: post-processing ------------------------------------------------


def test_strip_wrapping_quotes():
    assert strip_wrapping_quotes('"hello world"') == "hello world"
    assert strip_wrapping_quotes("`code take`") == "code take"
    assert strip_wrapping_quotes("no quotes here") == "no quotes here"


def test_html_to_text_strips_tags_entities_and_whitespace():
    raw = '<p>Hello &amp; <a href="http://x">welcome</a></p>\n\n  to it'
    assert html_to_text(raw) == "Hello & welcome to it"
    assert html_to_text(None) == ""
    assert html_to_text("") == ""


def test_strip_preamble_drops_known_scaffolding():
    assert (
        strip_preamble("Okay, let's do this. Here's the post: Markets are wild")
        == "Markets are wild"
    )
    assert strip_preamble("Sure. Prediction markets win") == "Prediction markets win"
    # No scaffolding → untouched, even when it starts with a bare 'this'.
    assert strip_preamble("This take stands alone") == "This take stands alone"
    # Never empties the post: a post that *is* only the filler is left as-is.
    assert strip_preamble("Here's the post:") == "Here's the post:"


def test_sanitize_formatting_strips_markdown_and_dashes():
    # Markdown emphasis/code is unwrapped to its inner text.
    assert sanitize_formatting("**bold** and `code`") == "bold and code"
    assert sanitize_formatting("a *witty* take") == "a witty take"
    # Em/en dashes and spaced hyphens become commas.
    assert sanitize_formatting("markets are wild — really wild") == (
        "markets are wild, really wild"
    )
    assert sanitize_formatting("a play - or a gamble") == "a play, or a gamble"
    # Less common dash codepoints models emit (horizontal bar, minus sign,
    # figure/two-em dash, fullwidth hyphen) are all normalized too.
    for dash in ("―", "−", "‒", "⸺", "－", "﹘"):
        assert sanitize_formatting(f"check {dash} either") == "check, either"
    assert sanitize_formatting("check—either") == "check, either"  # no spaces
    # Intra-word hyphens in names/numbers are preserved.
    assert sanitize_formatting("GPT-4 powers e-commerce") == "GPT-4 powers e-commerce"


def test_effective_length_counts_urls_as_23():
    url = "https://example.com/a/very/long/path/that/is/way/more/than/23/chars"
    text = f"check this {url}"
    # "check this " == 11 chars, + 23 for the URL.
    assert effective_length(text) == 11 + 23


# --- unit: tone selection -------------------------------------------------


def test_enabled_tones_returns_declared_enabled_in_order():
    cfg = PostTypesConfig.from_niche(_CONFIG)
    # _CONFIG enables news, spotlight, take — returned in canonical order.
    assert cfg.enabled_tones() == ["news", "spotlight", "take"]


def test_enabled_tones_falls_back_to_all_when_none_enabled():
    # No block → all tones; block that disables everything → all tones too.
    assert PostTypesConfig.from_niche({}).enabled_tones() == ALL_TONES
    all_off = {"post_types": {t: {"enabled": False} for t in ALL_TONES}}
    assert PostTypesConfig.from_niche(all_off).enabled_tones() == ALL_TONES


def test_shuffled_deck_deals_every_tone_before_repeating():
    tones = ["news", "spotlight", "take"]
    deck = shuffled_deck(tones)
    assert sorted(deck) == sorted(tones)  # a permutation, no dupes/drops


# --- no silent fallback ----------------------------------------------------


def test_no_fallback_raises_when_litellm_unavailable(monkeypatch):
    # A real provider with litellm missing must RAISE, not quietly degrade to the
    # title-echoing template provider.
    import sys

    from opensocial.ai.text import TextProviderError, get_text_provider

    monkeypatch.setitem(sys.modules, "litellm", None)  # `import litellm` → ImportError
    with pytest.raises(TextProviderError):
        get_text_provider({"ai": {"text": {"provider": "claude"}}})


def test_template_provider_only_when_explicitly_chosen():
    # provider: template is still honored (offline/tests) — it's a deliberate pick.
    from opensocial.ai.text import TemplateProvider, get_text_provider

    p = get_text_provider({"ai": {"text": {"provider": "template"}}})
    assert isinstance(p, TemplateProvider)


# --- unit: polish (free cleanup) -------------------------------------------


def test_hashtags_and_emoji_are_stripped():
    assert strip_hashtags_and_emoji("Big week for chips 🚀 #AI #Nvidia") == "Big week for chips"
    assert strip_hashtags_and_emoji("the #AI race and the #1 app") == "the AI race and the #1 app"


def test_cliche_filler_dropped_and_words_swapped():
    text, left = fix_cliches("Nvidia shipped Rubin. Let that sink in.\n\nIt's a game-changer.")
    assert "sink in" not in text
    assert "game-changer" not in text and "big deal" in text
    assert left == []
    _, left = fix_cliches("A rich tapestry of chips.")
    assert left == ["tapestry"]  # no safe fix: worth one rewrite


def test_polish_keeps_paragraph_breaks():
    text, _, _ = polish("**Nvidia** shipped Rubin.\n\n\n\nhonestly wild — fast too", 280)
    assert text == "Nvidia shipped Rubin.\n\nhonestly wild, fast too"


def test_fit_length_trims_whole_sentences():
    para1 = "Nvidia shipped its Rubin chip today. It is three times faster at inference."
    para2 = "I did not expect this so soon."
    text, needs_rewrite = fit_length(f"{para1}\n\n{para2}", 80)
    assert not needs_rewrite
    assert effective_length(text) <= 80
    assert text.endswith(".") and "…" not in text


def test_write_text_rewrites_only_when_code_cannot_fix():
    clean = _FixedProvider("Nvidia shipped Rubin.")
    write_text(clean, config=_CONFIG, post_type="news", subject="S")
    assert len(clean.calls) == 1  # nothing to fix: one call
    cliche = _FixedProvider("A tapestry of chips.", "Nvidia shipped Rubin.")
    out = write_text(cliche, config=_CONFIG, post_type="news", subject="S")
    assert out == "Nvidia shipped Rubin." and len(cliche.calls) == 2
    assert "tapestry" in cliche.calls[1][0]  # told what to avoid


def test_write_text_never_exceeds_the_limit():
    out = write_text(_FixedProvider("x" * 500), config=_CONFIG, post_type="news", subject="S")
    assert effective_length(out) <= 280


def test_empty_model_output_fails_loudly():
    from opensocial.ai.text import TextProviderError

    with pytest.raises(TextProviderError):
        write_text(_FixedProvider(""), config=_CONFIG, post_type="news", subject="S")


# --- unit: tone + samples --------------------------------------------------


def test_choose_tone_respects_enabled_and_avoids_repeat():
    import random

    rng = random.Random(1)
    for _ in range(30):
        assert choose_tone(_CONFIG, last_tone="news", rng=rng) in {"spotlight", "take"}


def test_niche_examples_override_account_samples():
    assert resolve_samples({}, ["acct"]) == ["acct"]
    assert resolve_samples({"persona": {"examples": ["niche"]}}, ["acct"]) == ["niche"]
    assert resolve_samples({"persona": {"examples": ["  "]}}, ["acct"]) == ["acct"]


# --- integration: knowledge base -> draft ----------------------------------


def _seed(session, items):
    store_items(session, items, NICHE)
    filter_niche(session, NICHE, _CONFIG)
    ingest(session)


def test_draft_next_writes_from_the_top_story_and_marks_it_used(session_factory):
    items = [
        _item("Open source AI model ships", url="https://a.com/1",
              media_urls=["https://img/1.png"]),
        _item("New dev framework released", url="https://b.com/2"),
    ]
    with session_factory() as session:
        _seed(session, items)
        first = draft_next(session, "wsA", NICHES, text_provider=TemplateProvider(),
                           image_provider=NoneProvider())
        second = draft_next(session, "wsA", NICHES, text_provider=TemplateProvider(),
                            image_provider=NoneProvider())
        rows = session.query(GeneratedPost).all()
        assert len(rows) == 2
        assert first.story_id != second.story_id  # never the same story twice
        assert {r.story_id for r in rows} == {first.story_id, second.story_id}
        assert all(r.content_item_id and r.platform_account_id == "wsA" for r in rows)
        assert all(r.text and "http" not in r.text and r.status == "draft" for r in rows)
        used = {u.story_id for u in session.query(StoryUsage).filter_by(status="used")}
        assert used == {first.story_id, second.story_id}


def test_synthesis_take_when_everything_is_used(session_factory):
    with session_factory() as session:
        _seed(session, [_item("Open source AI model ships", url="https://a.com/1")])
        draft_next(session, "wsA", NICHES, text_provider=TemplateProvider(),
                   image_provider=NoneProvider())
        take = draft_next(session, "wsA", NICHES, text_provider=TemplateProvider(),
                          image_provider=NoneProvider())
        assert take.picked_by == "synthesis" and take.post_type == "take"
        row = session.get(GeneratedPost, take.post_id)
        assert row.story_id is None and row.content_item_id is None


def test_empty_knowledge_base_writes_nothing(session_factory):
    with session_factory() as session:
        assert draft_next(session, "wsA", NICHES, text_provider=TemplateProvider()) is None
        assert session.query(GeneratedPost).count() == 0


def test_preview_persists_nothing(session_factory):
    with session_factory() as session:
        _seed(session, [_item("Open source AI model ships", url="https://a.com/1")])
        d = draft_next(session, "wsA", NICHES, persist=False,
                       text_provider=TemplateProvider(), image_provider=NoneProvider())
        assert d is not None and d.post_id is None
        assert session.query(GeneratedPost).count() == 0
        assert session.query(StoryUsage).count() == 0


def test_voice_samples_reach_the_prompt(session_factory):
    prov = _FixedProvider("Open source models keep getting better.")
    with session_factory() as session:
        _seed(session, [_item("Open source AI model ships", url="https://a.com/1",
                              summary="The model is 40% faster than last year's.")])
        draft_next(session, "wsA", NICHES, text_provider=prov, image_provider=NoneProvider(),
                   voice_samples=["i panic every time i read these"], fetch_article=False)
    system, user = prov.calls[-1]
    assert "i panic every time i read these" in system
    assert user.startswith("SUBJECT: Open source AI model ships")
    assert "40% faster" in user  # the fact sheet carries the summary


def test_regenerate_rewrites_from_the_same_story(session_factory):
    with session_factory() as session:
        _seed(session, [_item("Open source AI model ships", url="https://a.com/1")])
        d = draft_next(session, "wsA", NICHES, text_provider=TemplateProvider(),
                       image_provider=NoneProvider())
        post = session.get(GeneratedPost, d.post_id)
        old_type = post.post_type
        prov = _FixedProvider("A fresh angle on the open model.")
        regenerate_text(session, post, _CONFIG, text_provider=prov,
                        image_provider=NoneProvider())
        assert post.text == "A fresh angle on the open model."
        assert post.post_type != old_type
        assert prov.calls[-1][1].startswith("SUBJECT: Open source AI model ships")


# --- article extraction + fact sheet ---------------------------------------


def test_article_extraction_keeps_prose_and_skips_furniture():
    from opensocial.ai.article import extract_article_text, fetchable

    para = ("Nvidia said on Tuesday its Rubin chip delivers three times the "
            "inference speed of Blackwell.")
    html = (
        "<html><nav><p>Home | World | Business | Tech | Subscribe to our newsletter "
        "today</p></nav>"
        f"<article><h1>Rubin</h1><p>{para}</p><p>{para.replace('Tuesday', 'Monday')}</p>"
        f"<p>{para.replace('Nvidia', 'The company')}</p>"
        f"<p>{para.replace('three', 'four')}</p></article>"
        "<footer><p>All rights reserved. Copyright notice for this site goes right "
        "here.</p></footer></html>"
    )
    text = extract_article_text(html)
    assert text and "Rubin chip" in text
    assert "Subscribe" not in text and "rights reserved" not in text
    assert extract_article_text("<p>too short</p>") is None
    assert not fetchable("https://news.google.com/rss/articles/abc")
    assert fetchable("https://www.reuters.com/tech/x")


def test_factsheet_prefers_specific_sentences():
    from opensocial.ai.factsheet import build_factsheet

    article = (
        "Nvidia unveiled its Rubin chip on Tuesday. "
        "The weather was pleasant at the venue in San Jose. "
        "Rubin runs inference 3x faster than Blackwell, the company said. "
        "Shipments start in the second half of 2027."
    )
    facts = build_factsheet(title="Nvidia unveils Rubin chip", article=article,
                            entities={"nvidia", "rubin"}, budget=160)
    assert "3x faster" in facts
    assert "weather" not in facts
