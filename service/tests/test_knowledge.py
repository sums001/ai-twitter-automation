"""Knowledge base: grouping items into stories, free scoring, per-account memory."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from opensocial.core.db import (
    Base,
    ContentItemRow,
    Story,
    StoryUsage,
    delete_workspace,
    insert_generated_post,
    make_engine,
    store_items,
)
from opensocial.core.filtering import filter_niche, keyword_in
from opensocial.core.knowledge import (
    extract_entities,
    ingest,
    mark_shown,
    mark_used,
    postability,
    recent_top_stories,
    same_story,
    score_gap,
    shortlist,
    story_items,
    title_tokens,
)
from opensocial.core.models import ContentItem

NICHE = "tech"
CONFIG = {"filters": {"relevance_keywords": ["ai", "chip", "model"], "relevance_threshold": 1}}
NOW = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session_factory(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def _gn(title, outlet, *, hours_ago=1.0, summary=None):
    """A Google News item: headline carries ' - Outlet', outlet in <source>."""
    return ContentItem(
        source_name="googlenews",
        source_category="news",
        title=f"{title} - {outlet}",
        url=f"https://news.google.com/rss/articles/{abs(hash((title, outlet)))}",
        summary=summary,
        published_at=NOW - timedelta(hours=hours_ago),
        raw_metadata={"source": {"title": outlet, "href": f"https://{outlet}.com"}},
    )


def _hn(title, *, hours_ago=1.0):
    return ContentItem(
        source_name="hackernews",
        source_category="tech",
        title=title,
        url=f"https://example.com/{abs(hash(title))}",
        published_at=NOW - timedelta(hours=hours_ago),
    )


def _load(session_factory, items):
    with session_factory() as s:
        store_items(s, items, NICHE)
        filter_niche(s, NICHE, CONFIG)
        return ingest(s, now=NOW)


# ---- text features --------------------------------------------------------


def test_whole_word_keywords_no_substring_hits():
    assert keyword_in("openai ships a new ai model", "ai")
    assert keyword_in("nvidia sells more ais", "ai")  # plural
    assert not keyword_in("the ceo said nothing", "ai")  # "said" ≠ "ai"
    assert not keyword_in("parse the html", "ml")


def test_relevance_ignores_substrings(session_factory):
    with session_factory() as s:
        store_items(s, [_hn("The CEO said the quarter was fine")], NICHE)
        counts = filter_niche(s, NICHE, CONFIG)
    assert counts["filtered"] == 1  # "said" no longer counts as "ai"


def test_entities_and_figures():
    ents = extract_entities("Anthropic raises $30 billion at $350B valuation")
    assert "#$30b" in ents and "#$350b" in ents
    assert "openai" in extract_entities("OpenAI launches GPT-6")
    assert "gpt" in extract_entities("OpenAI launches GPT-6")


def test_same_story_rules():
    def sim(a, b):
        return same_story(title_tokens(a), extract_entities(a), title_tokens(b), extract_entities(b))

    assert sim("Microsoft pulls back from neocloud data center deals",
               "Microsoft Is Pulling Back From Key Neocloud Data Center Deals") > 0
    assert sim("Anthropic raises $30 billion at $350 billion valuation",
               "Anthropic closes $30B funding round valuing it at $350B") > 0
    # templated headlines about different companies are different stories
    assert sim("A Retiree Who Bought 1,000 Oracle Shares at $39 Just Watched Microsoft Walk Away",
               "A Retiree Who Bought 1,000 Tesla Shares at $197 Just Watched Musk Kill the Cybertruck") == 0
    assert sim("Tesla shares fall 5% after delivery miss",
               "Rivian shares fall 5% after delivery miss") == 0


def test_postability_penalizes_bait():
    good = postability("Nvidia unveils Rubin chip with 3x inference speed", {"nvidia"})
    bait = postability("Is Nvidia Fairly Valued After Its Big Run?", set())
    listicle = postability("5 Latest AI Stock Picks You Should Know", set())
    assert good > bait and good > listicle


# ---- ingest ---------------------------------------------------------------


def test_same_event_from_several_outlets_is_one_story(session_factory):
    result = _load(session_factory, [
        _gn("Nvidia unveils Rubin AI chip at GTC", "Reuters", hours_ago=3),
        _gn("Nvidia unveils Rubin AI chip with faster inference", "The Verge", hours_ago=2),
        _gn("Nvidia's new Rubin AI chip unveiled at GTC keynote", "CNBC", hours_ago=1),
        _gn("OpenAI releases a new open model for coding", "Reuters", hours_ago=1),
    ])
    assert result.items == 4 and result.new_stories == 2 and result.merged == 2
    with session_factory() as s:
        stories = {st.title: st for st in s.query(Story)}
        rubin = next(st for t, st in stories.items() if "Rubin" in t)
        assert rubin.item_count == 3
        assert sorted(rubin.outlets) == ["CNBC", "Reuters", "The Verge"]
        assert " - " not in rubin.title  # Google News outlet suffix stripped
        assert NICHE in rubin.niche_relevance


def test_ingest_is_idempotent_and_skips_filtered(session_factory):
    _load(session_factory, [_hn("New AI model beats benchmarks"), _hn("My cat is fluffy")])
    with session_factory() as s:
        again = ingest(s, now=NOW)
        assert again.items == 0
        assert s.query(Story).count() == 1  # the cat never passed the filter
        cat = s.query(ContentItemRow).filter(ContentItemRow.title == "My cat is fluffy").one()
        assert cat.story_id is None


def test_story_items_lead_with_best_item(session_factory):
    _load(session_factory, [
        _gn("Nvidia unveils Rubin AI chip at GTC", "Reuters", hours_ago=2),
        _hn("Nvidia unveils Rubin AI chip at GTC", hours_ago=1),
    ])
    with session_factory() as s:
        story = s.query(Story).one()
        items = story_items(s, story)
        assert items[0].id == story.best_item_id
        assert items[0].source_name == "googlenews"  # trust 0.75 > HN 0.65


# ---- scoring --------------------------------------------------------------


def test_coverage_outranks_single_outlet(session_factory):
    _load(session_factory, [
        _gn("Nvidia unveils Rubin AI chip at GTC", "Reuters", hours_ago=2),
        _gn("Nvidia unveils Rubin AI chip with faster inference", "The Verge", hours_ago=2),
        _gn("Nvidia's new Rubin AI chip unveiled at GTC keynote", "CNBC", hours_ago=2),
        _gn("Startup launches small AI model for phones", "TechBlog", hours_ago=2),
    ])
    with session_factory() as s:
        top = shortlist(s, "wsA", [NICHE], now=NOW)
    assert "Rubin" in top[0].story.title
    assert top[0].signals["coverage"] > top[1].signals["coverage"]


def test_used_is_per_account(session_factory):
    _load(session_factory, [_hn("New AI model beats benchmarks")])
    with session_factory() as s:
        story = s.query(Story).one()
        mark_used(s, "wsA", story.id)
        s.commit()
        assert shortlist(s, "wsA", [NICHE], now=NOW) == []
        assert len(shortlist(s, "wsB", [NICHE], now=NOW)) == 1  # B never posted it


def test_losers_fade(session_factory):
    _load(session_factory, [_hn("New AI model beats benchmarks")])
    with session_factory() as s:
        story = s.query(Story).one()
        before = shortlist(s, "wsA", [NICHE], now=NOW)[0].score
        mark_shown(s, "wsA", [story.id])
        mark_shown(s, "wsA", [story.id])
        s.commit()
        after = shortlist(s, "wsA", [NICHE], now=NOW)[0].score
    assert after == pytest.approx(before * 0.85 ** 2, rel=1e-3)


def test_posted_memory_excludes_repeats(session_factory):
    _load(session_factory, [_hn("Nvidia unveils Rubin AI chip at GTC", hours_ago=2)])
    with session_factory() as s:
        # A post this account already wrote about the same event (e.g. from a
        # different story row) makes the story a repeat for that account only.
        post = insert_generated_post(
            s, niche_slug=NICHE, post_type="news", ai_text_provider="t",
            text="Nvidia unveils Rubin AI chip at GTC and it is fast",
            platform_account_id="wsA",
        )
        post.created_at = NOW.replace(tzinfo=None)
        s.commit()
        assert shortlist(s, "wsA", [NICHE], now=NOW) == []
        assert len(shortlist(s, "wsB", [NICHE], now=NOW)) == 1


def test_niche_scoping_and_window(session_factory):
    _load(session_factory, [_hn("Old AI model news", hours_ago=100)])
    with session_factory() as s:
        assert shortlist(s, "wsA", [NICHE], now=NOW) == []  # outside 72h window
        assert shortlist(s, "wsA", ["finance"], now=NOW) == []


def test_recent_top_stories_includes_used(session_factory):
    _load(session_factory, [_hn("New AI model beats benchmarks")])
    with session_factory() as s:
        story = s.query(Story).one()
        mark_used(s, "wsA", story.id)
        s.commit()
        assert [st.id for st in recent_top_stories(s, [NICHE], now=NOW)] == [story.id]


def test_score_gap():
    class _S:
        def __init__(self, score):
            self.score = score

    assert score_gap([_S(1.0)]) == 1.0
    assert score_gap([_S(1.0), _S(0.5)]) == pytest.approx(0.5)


def test_delete_workspace_clears_only_its_story_memory(session_factory):
    _load(session_factory, [_hn("New AI model beats benchmarks")])
    with session_factory() as s:
        story = s.query(Story).one()
        mark_used(s, "wsA", story.id)
        mark_used(s, "wsB", story.id)
        s.commit()
        delete_workspace(s, "wsA")
        owners = {u.platform_account_id for u in s.query(StoryUsage)}
        assert owners == {"wsB"}
        assert s.query(Story).count() == 1  # the shared pool stays


# ---- lifetime / cleanup -------------------------------------------------------


def test_prune_forgets_stale_unused_stories_and_keeps_the_rest(session_factory):
    from opensocial.core.db import Command, Log, enqueue_command
    from opensocial.core.knowledge import prune

    _load(session_factory, [
        _hn("Fresh AI model news", hours_ago=2),
        _hn("Stale AI chip rumour", hours_ago=30),
        _hn("Old but posted AI model story", hours_ago=30),
        _hn("Ancient posted AI chip story", hours_ago=24 * 8),
    ])
    with session_factory() as s:
        by_title = {st.title: st.id for st in s.query(Story)}
        mark_used(s, "wsA", by_title["Old but posted AI model story"])
        mark_used(s, "wsA", by_title["Ancient posted AI chip story"])
        mark_shown(s, "wsB", [by_title["Stale AI chip rumour"]])  # shown ≠ used
        s.add(Log(level="info", message="old", logged_at=(NOW - timedelta(days=40)).replace(tzinfo=None)))
        old_cmd = enqueue_command(s, "fetch_sources")
        old_cmd.status = "done"
        old_cmd.created_at = (NOW - timedelta(days=10)).replace(tzinfo=None)
        s.commit()
        r = prune(s, now=NOW)
        left = {st.title for st in s.query(Story)}
    assert left == {"Fresh AI model news", "Old but posted AI model story"}
    assert r.stories == 2 and r.logs == 1 and r.commands == 1
    with session_factory() as s:
        assert s.query(StoryUsage).count() == 1  # only the kept posted story's
        assert s.query(Command).count() == 0


def test_prune_keeps_items_a_post_points_at(session_factory):
    from opensocial.core.knowledge import prune

    _load(session_factory, [_hn("Old AI model story", hours_ago=1), _hn("Other AI chip story", hours_ago=1)])
    with session_factory() as s:
        items = s.query(ContentItemRow).all()
        for it in items:
            it.fetched_at = (NOW - timedelta(days=10)).replace(tzinfo=None)
        insert_generated_post(
            s, niche_slug=NICHE, post_type="news", text="x", ai_text_provider="t",
            content_item_id=items[0].id,
        )
        s.commit()
        r = prune(s, now=NOW)
        assert r.items == 1
        assert [i.id for i in s.query(ContentItemRow)] == [items[0].id]


def test_story_ttl_is_adjustable_and_bounds_the_shortlist(session_factory):
    from opensocial.core.knowledge import prune, set_story_ttl_hours, story_ttl_hours

    _load(session_factory, [_hn("AI model story from yesterday", hours_ago=30)])
    with session_factory() as s:
        assert story_ttl_hours(s) == 24
        assert shortlist(s, "wsA", [NICHE], now=NOW) == []  # older than the TTL
        set_story_ttl_hours(s, 48)
        assert len(shortlist(s, "wsA", [NICHE], now=NOW)) == 1
        assert prune(s, now=NOW).stories == 0  # still inside 48h
        with pytest.raises(ValueError):
            set_story_ttl_hours(s, 0)


def test_forum_threads_are_less_postable():
    assert postability("Ask HN: Would banning LLM reverse engineering help OSS?", set()) < \
        postability("Hugging Face fixes admin API bypass", set())
