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
    text_provider=None,
    image_provider=None,
) -> GeneratedPost:
    """Re-run AI generation for this draft's subject, in place.

    Reuses the source item's title (or the independent topic) as the subject and
    keeps the post a publishable ``draft``.
    """
    from opensocial.ai.images import get_image_provider
    from opensocial.ai.text import get_text_provider
    from opensocial.core.db import ContentItemRow
    from opensocial.core.generate import DEFAULT_CHAR_LIMIT, _attach_image, _finalize_text

    text_provider = text_provider or get_text_provider(config)
    image_provider = image_provider or get_image_provider(config)
    niche_name = config.get("display_name") or post.niche_slug
    char_limit = int(config.get("char_limit", DEFAULT_CHAR_LIMIT))

    subject = f"an original {post.post_type} about {niche_name}"
    body = ""
    item_media = None
    if post.content_item_id:
        item = session.get(ContentItemRow, post.content_item_id)
        if item is not None:
            subject = item.title
            body = (item.summary or item.body or "")[:1500]
            item_media = item.media_urls

    post.text = _finalize_text(
        text_provider, config=config, post_type=post.post_type,
        subject=subject, body=body, char_limit=char_limit,
    )
    media_url, attribution, img_provider = _attach_image(
        image_provider, niche_name=niche_name,
        subject=subject, item_media=item_media,
        text_provider=text_provider, body=body,
    )
    post.media_url = media_url
    post.media_attribution = attribution
    post.ai_image_provider = img_provider
    post.ai_text_provider = text_provider.name
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
