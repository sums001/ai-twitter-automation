"""Source self-description and fetch skip reporting.

Each plugin declares its label, auth requirement and settings on its class; the
API and fetch runner read only that. A fetch that does nothing must say why
(source off, key missing, nothing followed) instead of a silent "no changes".
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import sessionmaker

from opensocial.core.commands import process_commands
from opensocial.core.config import load_all_niches
from opensocial.core.db import (
    Base,
    Command,
    enqueue_command,
    make_engine,
    source_statuses,
    upsert_source_status,
)
from opensocial.core.settings import set_followed_niches
from opensocial.sources import available_sources, get_source
from opensocial.sources.base import AUTH_NONE, AUTH_OPTIONAL, AUTH_REQUIRED


def test_every_source_describes_itself():
    for name in available_sources():
        cls = get_source(name)
        assert cls.label, f"{name} has no label"
        assert cls.description, f"{name} has no description"
        assert cls.auth in (AUTH_NONE, AUTH_OPTIONAL, AUTH_REQUIRED), name
        # a source that takes a key must say which env var holds it, and why
        assert bool(cls.key_envs) == (cls.auth != AUTH_NONE), name
        if cls.key_envs:
            assert cls.key_help, f"{name} takes a key but has no key_help"
        if cls.origin_key:
            assert cls.origin_label, name
            assert any(s.name == cls.origin_key for s in cls.settings), name


def test_removed_sources_are_gone():
    names = set(available_sources())
    assert not {"producthunt", "nasa", "github_releases"} & names
    # every niche references only registered sources
    for niche in load_all_niches("config/niches"):
        unknown = set(niche.sources) - names
        assert not unknown, f"{niche.slug} uses unknown sources {unknown}"


def test_missing_required_key(monkeypatch):
    guardian = get_source("guardian")
    monkeypatch.delenv("GUARDIAN_API_KEY", raising=False)
    assert guardian.missing_required_key()
    assert not guardian.missing_required_key({"api_key": "k"})
    monkeypatch.setenv("GUARDIAN_API_KEY", "k")
    assert not guardian.missing_required_key()
    # keyless sources never block on a key
    assert not get_source("hackernews").missing_required_key()


# ---- fetch skip reporting ----------------------------------------------------


@pytest.fixture()
def session_factory(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, future=True)


@pytest.fixture()
def config_dir(tmp_path):
    d = tmp_path / "niches"
    d.mkdir()
    niche = {
        "slug": "demo",
        "display_name": "Demo",
        "enabled": True,
        "sources": {"hackernews": {"query": "x"}, "guardian": {"query": "x"}},
    }
    (d / "demo.json").write_text(json.dumps(niche), encoding="utf-8")
    return str(d)


def _run_fetch(session_factory, config_dir, **payload):
    with session_factory() as s:
        enqueue_command(s, "fetch_sources", payload)
        s.commit()
    process_commands(session_factory, config_dir=config_dir)
    with session_factory() as s:
        cmd = s.query(Command).order_by(Command.id.desc()).first()
        return cmd.status, cmd.result


def _follow(session_factory, *slugs):
    with session_factory() as s:
        set_followed_niches(s, list(slugs))
        s.commit()


def test_fetch_with_nothing_followed_says_so(session_factory, config_dir):
    status, result = _run_fetch(session_factory, config_dir)
    assert status == "failed"
    assert "no niche is followed" in result["error"]


def test_fetch_of_disabled_source_says_so(session_factory, config_dir):
    _follow(session_factory, "demo")
    status, result = _run_fetch(session_factory, config_dir, source="hackernews")
    assert status == "failed"
    assert "Hacker News is turned off on the Sources page" in result["error"]


def test_fetch_skips_missing_key_without_error_status(
    session_factory, config_dir, monkeypatch
):
    monkeypatch.delenv("GUARDIAN_API_KEY", raising=False)
    _follow(session_factory, "demo")
    with session_factory() as s:
        upsert_source_status(s, "guardian", enabled=True)
    status, result = _run_fetch(session_factory, config_dir, source="guardian")
    assert status == "failed"
    assert "The Guardian needs an API key" in result["error"]
    # skipped, not attempted: no "error: …" fetch status recorded
    with session_factory() as s:
        assert source_statuses(s)["guardian"].last_fetch_status is None


def test_fetch_of_unused_source_says_so(session_factory, config_dir):
    _follow(session_factory, "demo")
    status, result = _run_fetch(session_factory, config_dir, source="reddit")
    assert status == "failed"
    assert "Reddit isn't used by any followed niche" in result["error"]
