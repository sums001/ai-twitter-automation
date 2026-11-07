"""Publishing: the retry state machine, cost recording, account credentials.

*When* to post is decided per account by ``core/autopilot.py`` (cadence,
window, caps); this module does the publishing itself. Each publish runs
through the retry state machine: a failure bumps ``post_attempts`` and records
``post_error``; after ``max_post_attempts`` the draft is ``failed``.

Publishing honors the dry-run fail-safe via the injected publisher. In dry-run
a ``post_history`` row is still recorded (status ``success``, cost as it would
be) so the dashboard reflects what *would* have gone out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from opensocial.core.config import niche_account_id
from opensocial.core.db import (
    GeneratedPost,
    PlatformAccount,
    default_platform_account,
    get_platform_account,
    list_platform_accounts,
    log,
    record_post_history,
)
from opensocial.core.settings import Settings
from opensocial.publish.base import Publisher, estimate_cost, get_publisher

# Statuses a post can be in and still be eligible to publish. With the approval
# queue retired this is just "draft" — published/rejected/failed are terminal.
ELIGIBLE_STATUSES = ("draft",)


@dataclass
class PublishOutcome:
    post_id: str
    post_type: str
    ok: bool
    dry_run: bool
    cost: float
    error: str | None = None


def _decrypt_account(
    session: Session, acct: PlatformAccount, settings: Settings
) -> dict | None:
    from opensocial.core.secrets import SecretsError, decrypt_credentials

    # No credentials enrolled yet → stay dry-run. Don't try to decrypt an empty
    # blob (Fernet would raise InvalidToken); a credential-less workspace simply
    # can't post live, which is the intended default.
    if not acct.credentials_encrypted:
        return None
    try:
        return decrypt_credentials(acct.credentials_encrypted, settings.secret_key)
    except SecretsError:
        log(session, "error", "platform credentials present but key missing/invalid")
        session.commit()
        return None


def load_credentials(
    session: Session, settings: Settings, *, platform: str = "x"
) -> dict | None:
    """Decrypt the default platform account's credentials, if one is enrolled.

    Returns ``None`` (so publishing stays dry-run) when no account exists or the
    key is missing/invalid, rather than raising mid-run. This is the
    single-account fallback; multi-account callers use
    :func:`load_credentials_for_account`.
    """
    acct = default_platform_account(session, platform=platform)
    if acct is None:
        return None
    return _decrypt_account(session, acct, settings)


def load_credentials_for_account(
    session: Session, settings: Settings, account_id: str | None
) -> dict | None:
    """Decrypt a specific account's credentials by id.

    Returns ``None`` (publishing stays dry-run) when the account is missing or
    the key is invalid, rather than raising mid-run.
    """
    acct = get_platform_account(session, account_id) if account_id else None
    if acct is None:
        return None
    return _decrypt_account(session, acct, settings)


def resolve_account_for_niche(
    session: Session, config: dict, *, platform: str = "x"
) -> PlatformAccount | None:
    """The account a niche should publish through.

    Uses the niche's stored ``account_id``. When the niche is unassigned, falls
    back to the sole enrolled account if exactly one exists; with zero or
    multiple accounts it returns ``None`` so the caller holds the niche rather
    than posting to the wrong account.
    """
    account_id = niche_account_id(config)
    if account_id:
        return get_platform_account(session, account_id)
    accounts = list_platform_accounts(session, platform=platform)
    return accounts[0] if len(accounts) == 1 else None


def _include_link(config: dict, post: GeneratedPost) -> bool:
    """Whether to append the source link. Off by default (cost strategy);
    never for independent posts (they have no source)."""
    if post.content_item_id is None:
        return False
    posting = (config or {}).get("posting") or {}
    return bool(posting.get("include_source_link", False))


def select_post(
    session: Session,
    *,
    platform_account_id: str | None = None,
    niche_slug: str | None = None,
    created_after: datetime | None = None,
) -> GeneratedPost | None:
    """The best eligible draft: highest priority, then newest.

    ``platform_account_id`` restricts to one workspace's drafts (niches are a
    shared catalog, so a workspace must never publish another's draft).
    ``created_after`` skips stale drafts so autopilot never posts old news.
    """
    stmt = select(GeneratedPost).where(GeneratedPost.status.in_(ELIGIBLE_STATUSES))
    if platform_account_id is not None:
        stmt = stmt.where(GeneratedPost.platform_account_id == platform_account_id)
    if niche_slug is not None:
        stmt = stmt.where(GeneratedPost.niche_slug == niche_slug)
    if created_after is not None:
        stmt = stmt.where(
            GeneratedPost.created_at >= created_after.astimezone(timezone.utc).replace(tzinfo=None)
        )
    return session.execute(
        stmt.order_by(
            GeneratedPost.priority_score.is_(None),  # non-null scores first
            GeneratedPost.priority_score.desc(),
            GeneratedPost.created_at.desc(),
        )
    ).scalars().first()


def publish_post(
    session: Session,
    post: GeneratedPost,
    config: dict,
    settings: Settings,
    *,
    publisher: Publisher | None = None,
    credentials: dict | None = None,
    platform_account_id: str | None = None,
) -> PublishOutcome:
    """Publish one draft through the retry state machine + cost recording.

    ``platform_account_id`` is the account this post went out through; it is
    recorded on the ``post_history`` row so history and per-account caps know
    who posted. Defaults to the draft's stamped account when not given.
    """
    publisher = publisher or get_publisher(settings, credentials=credentials)
    include_link = _include_link(config, post)
    cost = estimate_cost(included_source_link=include_link)
    account_id = platform_account_id or post.platform_account_id

    result = publisher.publish(text=post.text, media_url=post.media_url)
    now = datetime.now(timezone.utc)
    post.updated_at = now

    if result.ok:
        post.status = "published"
        post.scheduled_at = now
        record_post_history(
            session,
            generated_post_id=post.id,
            status="success",
            included_source_link=include_link,
            cost_estimate=cost,
            platform_account_id=account_id,
            platform_post_id=result.platform_post_id,
            platform_post_url=result.platform_post_url,
        )
        tag = "DRY-RUN would post" if result.dry_run else "published"
        log(session, "info", f"[{post.niche_slug}] {tag} <{post.post_type}> {post.id}")
        session.commit()
        return PublishOutcome(
            post_id=post.id, post_type=post.post_type, ok=True,
            dry_run=result.dry_run, cost=cost,
        )

    # Failure path — bump attempts, record error, maybe mark failed.
    post.post_attempts += 1
    post.post_error = result.error
    if post.post_attempts >= settings.max_post_attempts:
        post.status = "failed"
    record_post_history(
        session,
        generated_post_id=post.id,
        status="failed",
        included_source_link=include_link,
        cost_estimate=0.0,
        platform_account_id=account_id,
        error_message=result.error,
    )
    log(
        session, "error",
        f"[{post.niche_slug}] publish failed ({post.post_attempts}/"
        f"{settings.max_post_attempts}) {post.id}: {result.error}",
    )
    session.commit()
    return PublishOutcome(
        post_id=post.id, post_type=post.post_type, ok=False,
        dry_run=False, cost=0.0, error=result.error,
    )
