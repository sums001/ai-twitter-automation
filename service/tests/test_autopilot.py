"""Autopilot: one post every X minutes per account, inside its window."""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from opensocial.core.autopilot import (
    FETCH_LEAD,
    post_next,
    tick_all,
    tick_workspace,
    workspace_context,
)
from opensocial.core.db import (
    Base,
    GeneratedPost,
    PostHistory,
    StoryUsage,
    add_platform_account,
    insert_generated_post,
    make_engine,
    set_app_setting,
    store_items,
)
from opensocial.core.filtering import filter_niche
from opensocial.core.knowledge import ingest
from opensocial.core.models import ContentItem
from opensocial.core.scheduler import (
    Cadence,
    first_post_time,
    get_next_post_at,
    load_cadence,
    next_post_time,
    save_cadence,
    set_next_post_at,
)
from opensocial.core.settings import save_ai_config, set_followed_niches, set_scoped_setting
from opensocial.publish.base import Publisher, PublishResult

UTC = timezone.utc
NICHE = {
    "slug": "tech",
    "display_name": "Tech",
    "enabled": True,
    "filters": {"relevance_keywords": []},
    "post_types": {"news": {"enabled": True}, "take": {"enabled": True}},
    "image_source": "none",
}
ALL_DAY = Cadence(window=("00:00", "23:59"), every_minutes=60, jitter_pct=20,
                  min_gap_minutes=20)


class _OkPublisher(Publisher):
    dry_run = False

    def __init__(self):
        self.texts = []

    def publish(self, *, text, media_url=None):
        self.texts.append(text)
        return PublishResult(ok=True, platform_post_id="1", platform_post_url="https://x.com/1")


@pytest.fixture()
def session_factory(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


@pytest.fixture()
def config_dir(tmp_path):
    d = tmp_path / "niches"
    d.mkdir()
    (d / "tech.json").write_text(json.dumps(NICHE), encoding="utf-8")
    return str(d)


def _workspace(s, label="main", *, mode="auto", cap=None, cadence=ALL_DAY):
    acct = add_platform_account(s, account_label=label, credentials_encrypted=b"",
                                daily_post_cap=cap)
    set_followed_niches(s, ["tech"], acct.id)
    set_scoped_setting(s, acct.id, "app_mode", mode)
    set_scoped_setting(s, acct.id, "dry_run", "true")
    save_ai_config(s, {"text": {"provider": "template"}}, acct.id)
    save_cadence(s, acct.id, Cadence(**{**cadence.__dict__}))
    return acct.id


def _seed(s, *titles):
    now = datetime.now(UTC)
    items = [
        ContentItem(source_name="hackernews", source_category="tech", title=t,
                    url=f"https://example.com/{i}", published_at=now - timedelta(minutes=10))
        for i, t in enumerate(titles)
    ]
    store_items(s, items, "tech")
    filter_niche(s, "tech", NICHE)
    ingest(s)


def _ctx(s, wid, config_dir):
    return workspace_context(s, wid, config_dir)


def _due(s, wid, now):
    set_next_post_at(s, wid, now - timedelta(seconds=5))


# ---- cadence math ----------------------------------------------------------


def test_slots_are_evenly_spaced_across_the_window():
    from opensocial.core.scheduler import slot_minutes

    # 09:00-21:00 every 60 -> 12 slots at the middle of each hour
    slots = slot_minutes(Cadence(window=("09:00", "21:00"), every_minutes=60))
    assert len(slots) == 12 and slots[0] == 9 * 60 + 30 and slots[-1] == 20 * 60 + 30
    # 16:30-23:59 every 130 -> round(449/130) = 3 posts spread over the whole window
    slots = slot_minutes(Cadence(window=("16:30", "23:59"), every_minutes=130))
    assert len(slots) == 3
    gaps = {round(b - a, 6) for a, b in zip(slots, slots[1:])}
    assert len(gaps) == 1
    assert slots[0] - 16.5 * 60 == pytest.approx((23 * 60 + 59) - slots[-1])


def test_next_post_time_takes_the_next_slot_within_jitter():
    cad = Cadence(window=("09:00", "21:00"), every_minutes=60, jitter_pct=20)
    rng = random.Random(3)
    # posted at the 12:30 slot (a bit early or late): next is the 13:30 slot, +-6 min
    for posted_at in (datetime(2026, 11, 7, 12, 24, tzinfo=UTC), datetime(2026, 11, 7, 12, 36, tzinfo=UTC)):
        for _ in range(30):
            nxt = next_post_time(cad, posted_at, UTC, rng)
            slot = datetime(2026, 11, 7, 13, 30, tzinfo=UTC)
            assert abs(nxt - slot) <= timedelta(minutes=6)
    late = datetime(2026, 11, 7, 20, 30, tzinfo=UTC)  # the last slot
    nxt = next_post_time(cad, late, UTC, rng)
    assert nxt.date() == late.date() + timedelta(days=1)
    assert abs(nxt - datetime(2026, 11, 8, 9, 30, tzinfo=UTC)) <= timedelta(minutes=6)


def test_first_post_time_is_the_next_slot_from_now():
    cad = Cadence(window=("09:00", "21:00"), every_minutes=60, jitter_pct=0)
    rng = random.Random(1)
    assert first_post_time(cad, datetime(2026, 11, 7, 6, 0, tzinfo=UTC), UTC, rng) == \
        datetime(2026, 11, 7, 9, 30, tzinfo=UTC)
    assert first_post_time(cad, datetime(2026, 11, 7, 14, 0, tzinfo=UTC), UTC, rng) == \
        datetime(2026, 11, 7, 14, 30, tzinfo=UTC)
    assert first_post_time(cad, datetime(2026, 11, 7, 22, 0, tzinfo=UTC), UTC, rng) == \
        datetime(2026, 11, 8, 9, 30, tzinfo=UTC)


def test_cadence_validation():
    with pytest.raises(ValueError):
        Cadence(window=("21:00", "09:00")).validate()
    with pytest.raises(ValueError):
        Cadence(every_minutes=2).validate()
    with pytest.raises(ValueError):
        Cadence(jitter_pct=80).validate()


def test_cadence_seeds_from_legacy_config(session_factory, tmp_path):
    d = tmp_path / "legacy"
    d.mkdir()
    legacy = {**NICHE, "schedule": {"windows": [["08:00", "12:00"], ["14:00", "22:00"]],
                                    "posts_per_day": [2, 3]}}
    (d / "tech.json").write_text(json.dumps(legacy), encoding="utf-8")
    with session_factory() as s:
        acct = add_platform_account(s, account_label="a", credentials_encrypted=b"")
        set_followed_niches(s, ["tech"], acct.id)
        set_app_setting(s, "autopilot_fetch_minutes", "45")
        cad = load_cadence(s, acct.id, config_dir=d)
    assert cad.window == ("08:00", "22:00")
    assert cad.every_minutes == 45


def test_saving_cadence_rerolls_next_post(session_factory, config_dir):
    with session_factory() as s:
        wid = _workspace(s)
        set_next_post_at(s, wid, datetime.now(UTC) + timedelta(hours=3))
        save_cadence(s, wid, Cadence(every_minutes=30))
        assert get_next_post_at(s, wid) is None


# ---- ticks -----------------------------------------------------------------


def test_first_tick_schedules_then_waits(session_factory, config_dir):
    now = datetime.now(UTC)
    with session_factory() as s:
        wid = _workspace(s)
        _seed(s, "Open source AI model ships")
        out = tick_workspace(s, _ctx(s, wid, config_dir), now=now, tz=UTC)
        assert out.action == "waiting" and out.next_post_at > now
        out = tick_workspace(s, _ctx(s, wid, config_dir), now=now, tz=UTC)
        assert out.action == "waiting"
        assert s.query(GeneratedPost).count() == 0


def test_due_auto_tick_writes_publishes_and_reschedules(session_factory, config_dir):
    now = datetime.now(UTC)
    pub = _OkPublisher()
    with session_factory() as s:
        wid = _workspace(s)
        _seed(s, "Open source AI model ships", "New dev framework released")
        _due(s, wid, now)
        out = tick_workspace(s, _ctx(s, wid, config_dir), now=now, tz=UTC, publisher=pub)
        assert out.action == "posted"
        assert len(pub.texts) == 1
        post = s.query(GeneratedPost).one()
        assert post.status == "published" and post.story_id
        assert s.query(StoryUsage).filter_by(status="used").count() == 1
        nxt = get_next_post_at(s, wid)
        # the next slot at least half a spacing out (slots ~60 min apart, ±6 min nudge)
        assert timedelta(minutes=23) <= nxt - now <= timedelta(minutes=97)


def test_auto_prefers_a_fresh_waiting_draft(session_factory, config_dir):
    now = datetime.now(UTC)
    pub = _OkPublisher()
    with session_factory() as s:
        wid = _workspace(s)
        _seed(s, "Open source AI model ships")
        mine = insert_generated_post(
            s, niche_slug="tech", post_type="take", text="my hand written take",
            ai_text_provider="human", platform_account_id=wid, priority_score=0.5,
        )
        s.commit()
        out = post_next(s, _ctx(s, wid, config_dir), now=now, publisher=pub)
        assert out.action == "posted" and out.post_id == mine.id
        assert pub.texts == ["my hand written take"]
        assert s.query(GeneratedPost).count() == 1  # nothing new written


def test_manual_mode_always_drafts_on_every_mark(session_factory, config_dir):
    now = datetime.now(UTC)
    with session_factory() as s:
        wid = _workspace(s, mode="manual")
        _seed(s, "Open source AI model ships", "New dev framework released", "Rust ships async")
        actions = []
        for _ in range(3):
            _due(s, wid, now)
            actions.append(tick_workspace(s, _ctx(s, wid, config_dir), now=now, tz=UTC).action)
        assert actions == ["drafted", "drafted", "drafted"]  # no waiting-drafts limit
        assert s.query(GeneratedPost).filter_by(status="draft").count() == 3
        assert s.query(PostHistory).count() == 0  # manual never publishes


def test_auto_mark_drafts_then_publishes_best_fresh_draft(session_factory, config_dir):
    now = datetime.now(UTC)
    pub = _OkPublisher()
    with session_factory() as s:
        wid = _workspace(s)
        _seed(s, "Open source AI model ships")
        best = insert_generated_post(
            s, niche_slug="tech", post_type="take", text="high priority draft",
            ai_text_provider="human", platform_account_id=wid, priority_score=9.0,
        )
        stale = insert_generated_post(
            s, niche_slug="tech", post_type="take", text="old but even higher",
            ai_text_provider="human", platform_account_id=wid, priority_score=99.0,
        )
        stale.created_at = (now - timedelta(hours=7)).replace(tzinfo=None)
        s.commit()
        _due(s, wid, now)
        out = tick_workspace(s, _ctx(s, wid, config_dir), now=now, tz=UTC, publisher=pub)
        assert out.action == "posted" and out.post_id == best.id
        assert pub.texts == ["high priority draft"]  # stale draft skipped
        # a new draft was still written at the mark; it waits for a later one
        assert s.query(GeneratedPost).filter_by(status="draft").count() == 2


def test_min_gap_holds_the_post(session_factory, config_dir):
    now = datetime.now(UTC)
    pub = _OkPublisher()
    with session_factory() as s:
        wid = _workspace(s)
        _seed(s, "Open source AI model ships", "New dev framework released")
        assert post_next(s, _ctx(s, wid, config_dir), now=now, publisher=pub).action == "posted"
        _due(s, wid, now)
        out = tick_workspace(s, _ctx(s, wid, config_dir), now=now, tz=UTC, publisher=pub)
        assert out.action == "held" and "minimum gap" in out.detail
        assert out.next_post_at >= now + timedelta(minutes=19)


def test_daily_cap_holds_until_tomorrow(session_factory, config_dir):
    now = datetime.now(UTC)
    pub = _OkPublisher()
    with session_factory() as s:
        wid = _workspace(s, cap=1, cadence=Cadence(**{**ALL_DAY.__dict__, "min_gap_minutes": 0}))
        _seed(s, "Open source AI model ships", "New dev framework released")
        assert post_next(s, _ctx(s, wid, config_dir), now=now, publisher=pub).action == "posted"
        _due(s, wid, now)
        out = tick_workspace(s, _ctx(s, wid, config_dir), now=now, tz=UTC, publisher=pub)
        assert out.action == "held" and "daily cap" in out.detail
        assert out.next_post_at.date() > now.date()


def test_live_without_credentials_is_held(session_factory, config_dir):
    with session_factory() as s:
        wid = _workspace(s)
        set_scoped_setting(s, wid, "dry_run", "false")
        _seed(s, "Open source AI model ships")
        out = post_next(s, _ctx(s, wid, config_dir))
        assert out.action == "held" and "credentials" in out.detail
        assert s.query(GeneratedPost).count() == 0  # no tokens spent either


def test_no_followed_niches_is_idle(session_factory, config_dir):
    with session_factory() as s:
        wid = _workspace(s)
        set_followed_niches(s, [], wid)
        out = tick_workspace(s, _ctx(s, wid, config_dir), now=datetime.now(UTC), tz=UTC)
        assert out.action == "idle"


def test_used_stories_are_per_account(session_factory, config_dir):
    now = datetime.now(UTC)
    with session_factory() as s:
        a = _workspace(s, "a")
        b = _workspace(s, "b")
        _seed(s, "Open source AI model ships")
        assert post_next(s, _ctx(s, a, config_dir), now=now, publisher=_OkPublisher()).action == "posted"
        assert post_next(s, _ctx(s, b, config_dir), now=now, publisher=_OkPublisher()).action == "posted"
        posts = s.query(GeneratedPost).all()
        assert {p.platform_account_id for p in posts} == {a, b}
        assert len({p.story_id for p in posts}) == 1  # same story, one post per account


# ---- shared fetch ------------------------------------------------------------


def test_fetch_runs_just_before_a_post_and_is_throttled(session_factory, config_dir):
    calls = []

    def fake_fetch(sf, cd):
        calls.append(cd)

    now = datetime.now(UTC)
    with session_factory() as s:
        wid = _workspace(s)
        set_next_post_at(s, wid, now + FETCH_LEAD + timedelta(minutes=10))
    tick_all(session_factory, config_dir=config_dir, now=now, fetch=fake_fetch)
    assert calls == []  # not due soon: no fetch
    with session_factory() as s:
        set_next_post_at(s, wid, now + timedelta(minutes=3))
    tick_all(session_factory, config_dir=config_dir, now=now, fetch=fake_fetch)
    assert len(calls) == 1
    tick_all(session_factory, config_dir=config_dir, now=now + timedelta(minutes=1),
             fetch=fake_fetch)
    assert len(calls) == 1  # throttled


def test_cleanup_runs_at_most_hourly(session_factory):
    from opensocial.core.autopilot import maybe_prune

    now = datetime.now(UTC)
    assert maybe_prune(session_factory, now) is not None
    assert maybe_prune(session_factory, now + timedelta(minutes=30)) is None
    assert maybe_prune(session_factory, now + timedelta(minutes=61)) is not None


# ---------------------------------------------------------------------------
# Schedule display: projection across days + "will it actually run" status
# ---------------------------------------------------------------------------


def test_projection_continues_into_tomorrow_after_window_closes():
    from opensocial.core.scheduler import projected_times

    cad = Cadence(window=("08:00", "20:00"), every_minutes=60)
    now = datetime(2026, 11, 7, 23, 28, tzinfo=UTC)  # window already closed
    next_at = datetime(2026, 11, 8, 8, 33, tzinfo=UTC)  # 08:30 slot, jittered
    times = projected_times(cad, next_at, now, UTC)
    assert times[0] == next_at
    assert [t.strftime("%H:%M") for t in times[1:3]] == ["09:30", "10:30"]
    assert len(times) == 12 and times[-1].strftime("%H:%M") == "19:30"


def test_projection_spans_rest_of_today_then_tomorrow():
    from opensocial.core.scheduler import projected_times

    cad = Cadence(window=("08:00", "20:00"), every_minutes=120)  # slots 09,11,...,19
    now = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)
    times = projected_times(cad, datetime(2026, 11, 7, 15, 5, tzinfo=UTC), now, UTC)
    today = [t.hour for t in times if t.day == 7]
    tomorrow = [t.hour for t in times if t.day == 8]
    assert today == [15, 17, 19]
    assert tomorrow == [9, 11, 13, 15, 17, 19]


def test_schedule_status_states(session_factory, config_dir):
    from opensocial.core.autopilot import schedule_status

    now = datetime.now(UTC)
    with session_factory() as s:
        wid = _workspace(s, mode="manual")
        ctx = _ctx(s, wid, config_dir)
        assert schedule_status(s, ctx, now=now, next_at=now + timedelta(minutes=30))[0] == "ok"
        assert schedule_status(s, ctx, now=now, next_at=None)[0] == "starting"
        assert schedule_status(s, ctx, now=now, next_at=now - timedelta(minutes=30))[0] == "overdue"
        for i in range(5):  # a full queue no longer pauses anything
            insert_generated_post(s, niche_slug="tech", post_type="news", text=f"d{i}",
                                  ai_text_provider="template", platform_account_id=wid)
        s.commit()
        assert schedule_status(s, ctx, now=now, next_at=now + timedelta(minutes=30))[0] == "ok"
