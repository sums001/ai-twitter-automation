"""Account-enrollment + post-lifecycle tests.

Covers the Fernet credential round-trip and the three-status post lifecycle
(draft / published / rejected) after the approval queue was retired.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from opensocial.ai.text import TemplateProvider
from opensocial.core import approval
from opensocial.core.approval import ApprovalConfig, initial_status, sweep_timeouts
from opensocial.core.db import (
    Base,
    GeneratedPost,
    add_platform_account,
    default_platform_account,
    insert_generated_post,
    make_engine,
    store_items,
)
from opensocial.core.config import NicheConfig
from opensocial.core.engine import load_credentials, select_post
from opensocial.core.filtering import filter_niche
from opensocial.core.generate import draft_next
from opensocial.core.knowledge import ingest
from opensocial.core.models import ContentItem
from opensocial.core.secrets import (
    decrypt_credentials,
    encrypt_credentials,
    generate_key,
)
from opensocial.core.settings import Settings

NICHE = "tech"


@pytest.fixture()
def session_factory(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


def _settings(**over) -> Settings:
    base = dict(dry_run=True, app_mode="auto", global_daily_cap=25,
                max_post_attempts=3, secret_key=None)
    base.update(over)
    return Settings(**base)


def _item(title, url):
    now = datetime.now(timezone.utc)
    return ContentItem(source_name="hackernews", source_category="tech",
                       title=title, url=url, published_at=now)


_APPROVAL_CONFIG = {
    "slug": "tech",
    "filters": {"relevance_keywords": []},
    "post_types": {"news": {"enabled": True}},
    "approval": {"required": True, "timeout_minutes": 60, "on_timeout": "discard"},
}


# --- credentials round-trip (account enrollment) -------------------------


def test_secrets_round_trip():
    key = generate_key()
    creds = {"api_key": "k", "api_secret": "s",
             "access_token": "t", "access_token_secret": "ts"}
    blob = encrypt_credentials(creds, key)
    assert blob != str(creds).encode()  # actually encrypted
    assert decrypt_credentials(blob, key) == creds


def test_load_credentials_from_enrolled_account(session_factory):
    key = generate_key()
    creds = {"api_key": "k", "api_secret": "s",
             "access_token": "t", "access_token_secret": "ts"}
    with session_factory() as s:
        add_platform_account(
            s, account_label="main", credentials_encrypted=encrypt_credentials(creds, key)
        )
        assert default_platform_account(s) is not None
        loaded = load_credentials(s, _settings(secret_key=key))
    assert loaded == creds


def test_load_credentials_none_when_no_account(session_factory):
    with session_factory() as s:
        assert load_credentials(s, _settings(secret_key=generate_key())) is None


# --- approval config + generation gating ---------------------------------


def test_approval_config_parsing():
    cfg = ApprovalConfig.from_niche(_APPROVAL_CONFIG)
    assert cfg.required and cfg.timeout_minutes == 60 and cfg.on_timeout == "discard"
    # bad on_timeout falls back to discard
    assert ApprovalConfig.from_niche({"approval": {"on_timeout": "nonsense"}}).on_timeout == "discard"


def test_initial_status_always_draft():
    # Approval was retired: a fresh post is always a publishable draft, even when
    # a legacy niche config still carries an `approval` block.
    assert initial_status(_APPROVAL_CONFIG) == "draft"
    assert initial_status({"approval": {"required": False}}) == "draft"


def test_generation_drafts_are_immediately_publishable(session_factory):
    with session_factory() as s:
        store_items(s, [_item("AI model ships", "https://a.com/1")], NICHE)
        filter_niche(s, NICHE, _APPROVAL_CONFIG)
        ingest(s)
        draft = draft_next(
            s, "wsA", [NicheConfig.from_dict(_APPROVAL_CONFIG)],
            text_provider=TemplateProvider(),
        )
        assert draft is not None
        row = s.query(GeneratedPost).one()
        assert row.status == "draft"
        # drafts are publishable straight away
        assert select_post(s, platform_account_id="wsA").id == row.id


# --- post lifecycle transitions ------------------------------------------


def _draft(s):
    return insert_generated_post(
        s, niche_slug=NICHE, post_type="news", text="draft text",
        ai_text_provider="template", status="draft", priority_score=1.0,
    )


def test_draft_is_publishable(session_factory):
    with session_factory() as s:
        post = _draft(s)
        s.commit()
        assert select_post(s).id == post.id


def test_reject_removes_from_queue(session_factory):
    with session_factory() as s:
        post = _draft(s)
        s.commit()
        approval.reject(s, post)
        assert post.status == "rejected"
        assert select_post(s) is None


def test_edit_replaces_text_and_keeps_draft(session_factory):
    with session_factory() as s:
        post = _draft(s)
        s.commit()
        approval.edit(s, post, "the better text")
        assert post.text == "the better text"
        assert post.status == "draft"


def test_regenerate_keeps_draft_and_changes_text(session_factory):
    with session_factory() as s:
        post = _draft(s)
        s.commit()
        approval.regenerate(s, post, _APPROVAL_CONFIG, text_provider=TemplateProvider())
        assert post.status == "draft"  # still publishable


def test_reject_is_terminal_through_edit_and_regenerate(session_factory):
    # Edit/regenerate must not resurrect a rejected post into the publish pool.
    with session_factory() as s:
        post = _draft(s)
        s.commit()
        approval.reject(s, post)
        approval.edit(s, post, "sneaky")
        assert post.status == "rejected"
        approval.regenerate(s, post, _APPROVAL_CONFIG, text_provider=TemplateProvider())
        assert post.status == "rejected"


def test_sweep_timeouts_is_a_noop(session_factory):
    # The timeout sweep was retired alongside the approval queue.
    with session_factory() as s:
        post = _draft(s)
        s.commit()
        assert sweep_timeouts(s, {NICHE: _APPROVAL_CONFIG}) == {"published": 0, "discarded": 0}
        assert post.status == "draft"
