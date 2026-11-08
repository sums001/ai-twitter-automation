"""Post lifecycle transitions (logic layer).

The approval queue was retired: a post has only three statuses — ``draft``,
``published`` and ``rejected``. Fresh drafts are immediately publishable; the
dashboard drives edit / regenerate / reject (and a post-now), all of which leave
a post a publishable ``draft`` until it is published or rejected.

This module is the pure logic (state transitions), called from the dashboard
command bridge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from opensocial.core.db import GeneratedPost, log


@dataclass
class ApprovalConfig:
    required: bool = False
    timeout_minutes: int | None = None  # None = wait forever
    on_timeout: str = "discard"  # "publish" | "discard"

    @classmethod
    def from_niche(cls, raw: dict) -> "ApprovalConfig":
        a = (raw or {}).get("approval") or {}
        on_timeout = str(a.get("on_timeout", "discard")).lower()
        if on_timeout not in ("publish", "discard"):
            on_timeout = "discard"
        timeout = a.get("timeout_minutes")
        return cls(
            required=bool(a.get("required", False)),
            timeout_minutes=int(timeout) if timeout is not None else None,
            on_timeout=on_timeout,
        )


def initial_status(config: dict) -> str:
    """The status a freshly generated draft should get.

    The approval queue was retired: there are only three post statuses now —
    ``draft``, ``published`` and ``rejected``. Every fresh draft starts as
    ``draft`` (immediately eligible for the publisher); the per-niche
    ``approval`` block is no longer consulted here.
    """
    return "draft"


def _touch(post: GeneratedPost) -> None:
    post.updated_at = datetime.now(timezone.utc)


def approve(session: Session, post: GeneratedPost) -> GeneratedPost:
    """Confirm a draft. With approval retired this just keeps it as a publishable
    ``draft``."""
    if post.status not in ("published", "rejected"):
        post.status = "draft"
    _touch(post)
    log(session, "info", f"[{post.niche_slug}] kept draft {post.id}")
    session.commit()
    return post


def reject(session: Session, post: GeneratedPost) -> GeneratedPost:
    post.status = "rejected"
    _touch(post)
    log(session, "info", f"[{post.niche_slug}] rejected {post.id}")
    session.commit()
    return post


def edit(session: Session, post: GeneratedPost, new_text: str) -> GeneratedPost:
    """Replace the text, keeping the post a publishable ``draft``."""
    post.text = new_text
    if post.status not in ("published", "rejected"):
        post.status = "draft"
    _touch(post)
    log(session, "info", f"[{post.niche_slug}] edited {post.id}")
    session.commit()
    return post


def regenerate(
    session: Session,
    post: GeneratedPost,
    config: dict,
    *,
    voice_samples: list[str] | None = None,
    text_provider=None,
    image_provider=None,
) -> GeneratedPost:
    """Rewrite this draft from the same story in a new tone, in place.

    Keeps the post a publishable ``draft`` (unless it was already terminal).
    """
    from opensocial.core.generate import regenerate_text

    regenerate_text(
        session, post, config, voice_samples=voice_samples,
        text_provider=text_provider, image_provider=image_provider,
    )
    if post.status not in ("published", "rejected"):
        post.status = "draft"
    _touch(post)
    log(session, "info", f"[{post.niche_slug}] regenerated {post.id}")
    session.commit()
    return post


def sweep_timeouts(
    session: Session, config_by_niche: dict[str, dict], *, now: datetime | None = None
) -> dict[str, int]:
    """No-op retained for the scheduler tick.

    The approval queue (and its timeout policy) was retired, so there is nothing
    to sweep. Kept as a stable entry point the CLI tick still calls.
    """
    return {"published": 0, "discarded": 0}
