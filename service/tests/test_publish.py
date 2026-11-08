"""Phase 4 tests — slot resolution, best-at-slot publish, the retry state
machine, caps, dry-run fail-safe, cost, and the command bridge.

All publishing uses an injected fake publisher so nothing hits the network.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from opensocial.core.db import (
    Base,
    GeneratedPost,
    Log,
    PostHistory,
    enqueue_command,
    insert_generated_post,
    make_engine,
    published_today_count,
)
from opensocial.core.engine import (
    publish_post,
    select_post,
)
from opensocial.core.settings import Settings
from opensocial.publish.base import (
    COST_TEXT_ONLY,
    COST_WITH_LINK,
    DryRunPublisher,
    Publisher,
    PublishResult,
    estimate_cost,
)

NICHE = "tech"

_CONFIG = {
    "slug": "tech",
    "post_types": {
        "news": {"enabled": True},
        "take": {"enabled": True},
    },
    "posting": {"include_source_link": False},
}


@pytest.fixture()
def session_factory(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def _auto(**over) -> Settings:
    base = dict(
        dry_run=True, app_mode="auto", global_daily_cap=25,
        max_post_attempts=3, secret_key=None,
    )
    base.update(over)
    return Settings(**base)


class _OkPublisher(Publisher):
    dry_run = False

    def publish(self, *, text, media_url=None):
        return PublishResult(ok=True, platform_post_id="123",
                             platform_post_url="https://x.com/i/web/status/123")


class _FailPublisher(Publisher):
    dry_run = False

    def publish(self, *, text, media_url=None):
        return PublishResult(ok=False, error="boom")


def _draft(session, *, ptype="news", score=1.0, independent=False, account_id=None):
    return insert_generated_post(
        session,
        niche_slug=NICHE,
        post_type=ptype,
        text=f"a {ptype} post",
        ai_text_provider="template",
        content_item_id=None if independent else None,
        priority_score=score,
        platform_account_id=account_id,
    )


# --- cost model ----------------------------------------------------------


def test_cost_model():
    assert estimate_cost(included_source_link=False) == COST_TEXT_ONLY
    assert estimate_cost(included_source_link=True) == COST_WITH_LINK


# --- draft selection ----------------------------------------------------


def test_select_picks_highest_priority(session_factory):
    with session_factory() as s:
        _draft(s, ptype="news", score=0.2)
        best = _draft(s, ptype="news", score=0.9)
        s.commit()
        chosen = select_post(s)
        assert chosen.id == best.id


def test_select_post_is_scoped_to_workspace(session_factory):
    # Niches are shared, so two workspaces can hold drafts for the same niche.
    # A workspace must only ever publish its *own* draft — even when another
    # workspace's draft for that niche has a higher priority score.
    with session_factory() as s:
        a = _draft(s, ptype="news", score=0.9, account_id="wsA")
        b = _draft(s, ptype="news", score=0.5, account_id="wsB")
        s.commit()
        assert select_post(s, platform_account_id="wsB").id == b.id
        assert select_post(s, platform_account_id="wsA").id == a.id
        # Stale drafts are skipped when a freshness bound is given.
        later = datetime.now(timezone.utc) + timedelta(minutes=1)
        assert select_post(s, platform_account_id="wsA", created_after=later) is None
        # Unscoped (legacy) selection still returns the global best.
        assert select_post(s).id == a.id


def test_select_is_priority_only_with_no_per_type_caps(session_factory):
    with session_factory() as s:
        # Publish one take, then add fresh drafts. Per-tone caps no longer exist,
        # so selection is purely by priority — a second take is NOT skipped.
        published = _draft(s, ptype="take", score=0.5)
        s.commit()
        publish_post(s, published, _CONFIG, _auto(), publisher=_OkPublisher())
        take = _draft(s, ptype="take", score=0.9)
        _draft(s, ptype="news", score=0.1)
        s.commit()
        chosen = select_post(s)
        assert chosen.id == take.id  # highest priority wins, no cap fall-through


# --- dry-run fail-safe + cost recording ----------------------------------


def test_dry_run_records_history_without_posting(session_factory):
    with session_factory() as s:
        post = _draft(s, ptype="news")
        s.commit()
        outcome = publish_post(s, post, _CONFIG, _auto(dry_run=True),
                               publisher=DryRunPublisher())
        assert outcome.ok and outcome.dry_run
        assert post.status == "published"
        hist = s.query(PostHistory).one()
        assert hist.status == "success"
        assert hist.cost_estimate == COST_TEXT_ONLY
        assert hist.platform_post_id is None  # nothing really posted


def test_link_post_costs_more(session_factory):
    cfg = {**_CONFIG, "posting": {"include_source_link": True}}
    with session_factory() as s:
        # a source-derived post (content_item_id set) so the link applies
        post = insert_generated_post(
            s, niche_slug=NICHE, post_type="news", text="x",
            ai_text_provider="template", content_item_id="cid-1",
            priority_score=1.0,
        )
        s.commit()
        outcome = publish_post(s, post, cfg, _auto(), publisher=_OkPublisher())
        assert outcome.cost == COST_WITH_LINK


# --- retry state machine -------------------------------------------------


def test_failure_increments_attempts_then_fails(session_factory):
    settings = _auto(max_post_attempts=2)
    with session_factory() as s:
        post = _draft(s, ptype="news")
        s.commit()
        publish_post(s, post, _CONFIG, settings, publisher=_FailPublisher())
        assert post.post_attempts == 1
        assert post.status != "failed"  # still has a retry left
        assert post.post_error == "boom"
        publish_post(s, post, _CONFIG, settings, publisher=_FailPublisher())
        assert post.post_attempts == 2
        assert post.status == "failed"  # max attempts reached


def test_decrypt_bad_blob_raises_secretserror_not_invalidtoken():
    # decrypt_credentials must surface a catchable SecretsError on a key
    # mismatch / garbage blob, never the raw cryptography InvalidToken.
    from opensocial.core.secrets import (
        SecretsError,
        encrypt_credentials,
        generate_key,
    )

    blob = encrypt_credentials({"k": "v"}, generate_key())
    with pytest.raises(SecretsError):
        from opensocial.core.secrets import decrypt_credentials

        decrypt_credentials(blob, generate_key())  # different key


# --- command bridge ------------------------------------------------------


def test_command_queue_runs_post_now(session_factory, tmp_path, monkeypatch):
    # post_now should publish the named draft and mark the command done.
    from opensocial.core import commands as commands_mod

    with session_factory() as s:
        post = _draft(s, ptype="news")
        s.commit()
        post_id = post.id
        enqueue_command(s, "post_now", {"generated_post_id": post_id})

    # Force dry-run + an empty config dir (post_now tolerates missing config).
    monkeypatch.setenv("POST_DRY_RUN", "true")
    ran = commands_mod.process_commands(
        session_factory, config_dir=str(tmp_path), settings=_auto(dry_run=True)
    )
    assert ran == 1
    with session_factory() as s:
        assert s.get(GeneratedPost, post_id).status == "published"
        from opensocial.core.db import Command

        cmd = s.query(Command).one()
        assert cmd.status == "done"
        assert s.query(Log).count() >= 1  # log mirror populated


# --- multi-account routing -----------------------------------------------


def test_resolve_account_falls_back_to_sole_account(session_factory):
    from opensocial.core.db import PlatformAccount
    from opensocial.core.engine import resolve_account_for_niche

    with session_factory() as s:
        # No accounts → unassigned niche resolves to None.
        assert resolve_account_for_niche(s, _CONFIG) is None
        # Exactly one account → unassigned niche falls back to it.
        s.add(PlatformAccount(
            id="only", platform="x", account_label="only",
            credentials_encrypted=b"x",
        ))
        s.commit()
        assert resolve_account_for_niche(s, _CONFIG).id == "only"
        # A second account makes the fallback ambiguous → None (held).
        s.add(PlatformAccount(
            id="second", platform="x", account_label="second",
            credentials_encrypted=b"x",
        ))
        s.commit()
        assert resolve_account_for_niche(s, _CONFIG) is None
        # An explicit account_id always wins, regardless of count.
        cfg = {**_CONFIG, "account_id": "second"}
        assert resolve_account_for_niche(s, cfg).id == "second"


def test_publish_records_posting_account(session_factory):
    # The draft's stamped account is recorded on the post_history row when no
    # explicit account is passed.
    with session_factory() as s:
        post = insert_generated_post(
            s, niche_slug=NICHE, post_type="news", text="x",
            ai_text_provider="template", priority_score=1.0,
            platform_account_id="acctZ",
        )
        s.commit()
        out = publish_post(s, post, _CONFIG, _auto(), publisher=_OkPublisher())
        assert out.ok
        hist = s.query(PostHistory).one()
        assert hist.status == "success"
        assert hist.platform_account_id == "acctZ"
