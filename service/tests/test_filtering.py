"""Phase 2 tests — filtering, near-duplicate detection, and prioritization.

Runs entirely against an in-memory SQLite DB with hand-built content, so no
network or live sources are involved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from opensocial.core.db import Base, make_engine, store_items
from opensocial.core.db import ContentItemNiche
from opensocial.core.filtering import filter_niche
from opensocial.core.models import ContentItem

NICHE = "tech"


@pytest.fixture()
def session_factory(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def _item(title, *, url=None, body="", published=None, engagement=None, sentiment=None):
    now = datetime.now(timezone.utc)
    return ContentItem(
        source_name="hackernews",
        source_category="tech",
        title=title,
        url=url or f"https://example.com/{abs(hash(title))}",
        body=body,
        published_at=published or now,
        engagement=engagement,
        sentiment=sentiment,
    )


def test_blocklist_and_relevance_and_age(session_factory):
    old = datetime.now(timezone.utc) - timedelta(days=30)
    items = [
        _item("New open source AI model released"),        # relevant, fresh -> candidate
        _item("Free crypto giveaway, claim now"),          # blocklisted -> filtered
        _item("My cat is very fluffy today"),              # no relevance kw -> filtered
        _item("AI breakthrough from years ago", published=old),  # too old -> filtered
    ]
    config = {
        "filters": {
            "blocklist": ["giveaway"],
            "relevance_keywords": ["ai", "open source", "model"],
            "relevance_threshold": 1,
            "max_age_days": 7,
        }
    }

    with session_factory() as session:
        store_items(session, items, NICHE)
        counts = filter_niche(session, NICHE, config)

    assert counts == {"candidate": 1, "filtered": 3, "duplicate": 0}


def test_near_duplicate_detection(session_factory):
    items = [
        _item("OpenAI releases new GPT model for developers", url="https://a.com/1"),
        _item("OpenAI releases new GPT model for developers today", url="https://b.com/2"),
        _item("Rust 2.0 ships with async overhaul", url="https://c.com/3"),
    ]
    config = {"filters": {"relevance_keywords": [], "dup_threshold": 0.7}}

    with session_factory() as session:
        store_items(session, items, NICHE)
        counts = filter_niche(session, NICHE, config)

    assert counts["candidate"] == 2
    assert counts["duplicate"] == 1


def test_near_duplicate_respects_recent_window(session_factory):
    now = datetime.now(timezone.utc)
    items = [
        _item("OpenAI releases new GPT model for developers",
              url="https://a.com/1", published=now - timedelta(days=10)),
        # Same story but seen 10 days later — outside the dup window, so it is a
        # fresh candidate, not a duplicate of the stale one.
        _item("OpenAI releases new GPT model for developers",
              url="https://b.com/2", published=now),
    ]
    config = {
        "filters": {
            "relevance_keywords": [],
            "dup_threshold": 0.7,
            "dup_window_days": 3,
        }
    }

    with session_factory() as session:
        store_items(session, items, NICHE)
        counts = filter_niche(session, NICHE, config)

    assert counts["candidate"] == 2
    assert counts["duplicate"] == 0


def test_headline_hits_weigh_more_than_body_hits(session_factory):
    items = [
        _item("New AI chip announced", url="https://a.com/1"),
        _item("Company reports results", url="https://b.com/2", body="It mentions ai once."),
    ]
    config = {"filters": {"relevance_keywords": ["ai"], "relevance_threshold": 1}}
    with session_factory() as session:
        store_items(session, items, NICHE)
        filter_niche(session, NICHE, config)
        scores = {
            link.content_item_id: link.relevance_score
            for link in session.query(ContentItemNiche)
        }
    by_title = {i.title: scores[i.id] for i in items}
    assert by_title["New AI chip announced"] > by_title["Company reports results"] > 0


def test_duplicates_keep_relevance_for_the_knowledge_base(session_factory):
    items = [
        _item("OpenAI releases new GPT model for developers", url="https://a.com/1"),
        _item("OpenAI releases new GPT model for developers today", url="https://b.com/2"),
    ]
    config = {"filters": {"relevance_keywords": ["model"], "dup_threshold": 0.7}}
    with session_factory() as session:
        store_items(session, items, NICHE)
        filter_niche(session, NICHE, config)
        links = session.query(ContentItemNiche).all()
    dup = next(link for link in links if link.status == "duplicate")
    assert dup.relevance_score > 0  # counted as extra coverage downstream
