"""The optional AI pick: only on close calls, and never blocking a post."""

from __future__ import annotations

from types import SimpleNamespace

from opensocial.ai.ranking import CLEAR_LEAD, pick_story
from opensocial.ai.text import TemplateProvider


class _FakeProvider:
    name = "fake"

    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0
        self.last_user = ""

    def generate(self, system: str, user: str) -> str:
        self.calls += 1
        self.last_user = user
        return self.reply


class _BoomProvider:
    name = "boom"

    def generate(self, system: str, user: str) -> str:
        raise RuntimeError("model down")


def _scored(*scores):
    return [
        SimpleNamespace(
            score=s,
            outlet_count=i + 1,
            story=SimpleNamespace(title=f"Story {i + 1}", outlets=["x"] * (i + 1)),
        )
        for i, s in enumerate(scores)
    ]


def test_clear_leader_skips_the_model():
    prov = _FakeProvider("2")
    cands = _scored(1.0, 1.0 - CLEAR_LEAD - 0.01)
    winner, how = pick_story(cands, text_provider=prov)
    assert winner is cands[0] and how == "score"
    assert prov.calls == 0  # zero tokens spent


def test_close_call_asks_the_model():
    prov = _FakeProvider("2")
    cands = _scored(0.50, 0.49, 0.30)
    winner, how = pick_story(cands, text_provider=prov, recent_posts=["Earlier post"])
    assert winner is cands[1] and how == "ai"
    assert prov.calls == 1
    assert "Earlier post" in prov.last_user
    assert "Story 2 (2 outlets)" in prov.last_user


def test_offline_and_single_take_the_top():
    cands = _scored(0.5, 0.49)
    assert pick_story(cands, text_provider=TemplateProvider())[1] == "score"
    assert pick_story(cands[:1], text_provider=_FakeProvider("2"))[0] is cands[0]


def test_bad_reply_or_failure_falls_back_to_top():
    cands = _scored(0.5, 0.49)
    assert pick_story(cands, text_provider=_FakeProvider("no idea"))[0] is cands[0]
    assert pick_story(cands, text_provider=_FakeProvider("9"))[0] is cands[0]
    assert pick_story(cands, text_provider=_BoomProvider())[0] is cands[0]
