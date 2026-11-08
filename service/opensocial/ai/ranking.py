"""The one optional LLM ranking call: pick which shortlisted story to post.

The knowledge base already ranks stories for free (``core/knowledge.py``).
:func:`pick_story` only asks the model when that ranking has no clear winner:
when the top story leads the runner-up by ``CLEAR_LEAD`` or more, or when
running offline, the top story is taken as is. The prompt carries headlines
plus the account's last few posts, roughly 250 tokens in and one number out.

Any model or parse failure falls back to the top story, so a misbehaving model
never blocks a post.
"""

from __future__ import annotations

import re

from opensocial.ai.prompts import build_pick_messages
from opensocial.ai.text import TextProvider
from opensocial.core.knowledge import score_gap

# Relative score lead at which the free ranking decides on its own.
CLEAR_LEAD = 0.15
# How many shortlisted stories the model chooses between.
PICK_FROM = 4

_INT_RE = re.compile(r"\d+")


def is_offline(provider: TextProvider) -> bool:
    """The template provider can't judge stories; it always takes the top one."""
    return getattr(provider, "name", "") == "template"


def _label(scored) -> str:
    n = scored.outlet_count
    return f"{scored.story.title} ({n} outlets)" if n > 1 else scored.story.title


def pick_story(
    shortlisted: list,
    *,
    text_provider: TextProvider,
    recent_posts: list[str] | None = None,
    niche_names: list[str] | None = None,
) -> tuple[object, str]:
    """Return ``(winner, how)`` where ``how`` is ``"score"`` or ``"ai"``.

    ``shortlisted`` is best-first (:func:`~opensocial.core.knowledge.shortlist`).
    """
    if not shortlisted:
        raise ValueError("pick_story needs at least one story")
    top = shortlisted[0]
    if len(shortlisted) == 1 or is_offline(text_provider):
        return top, "score"
    if score_gap(shortlisted) >= CLEAR_LEAD:
        return top, "score"

    options = shortlisted[:PICK_FROM]
    system, user = build_pick_messages(
        niche_names=niche_names or [],
        stories=[_label(s) for s in options],
        recent_posts=recent_posts or [],
    )
    try:
        reply = text_provider.generate(system, user)
    except Exception:
        return top, "score"
    for match in _INT_RE.findall(reply or ""):
        idx = int(match) - 1
        if 0 <= idx < len(options):
            return options[idx], "ai"
    return top, "score"
