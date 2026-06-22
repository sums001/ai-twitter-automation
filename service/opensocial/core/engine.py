"""Phase 4 publish engine — best-at-slot-time selection + state machine.

When a slot is due the engine picks the **best-scoring eligible queued post at
that moment** (not a pre-assigned one), so fresh high-priority content can jump
the queue. It enforces, in order: the global daily cap, the per-niche slot
count, the minimum gap since the last publish, and per-type daily caps. Each
publish runs through the retry state machine: a failure bumps ``post_attempts``
and records ``post_error``; after ``max_post_attempts`` the draft is ``failed``.

Publishing honors the dry-run fail-safe via the injected publisher — in dry-run
a ``post_history`` row is still recorded (status ``success``, cost as it would
be) so the dashboard reflects what *would* have gone out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from opensocial.core.config import niche_account_id
from opensocial.core.db import (
    GeneratedPost,
    PlatformAccount,
    default_platform_account,
    get_platform_account,
    last_published_at,
    list_platform_accounts,
    log,
    published_today_count,
    record_post_history,
)
from opensocial.core.scheduler import ScheduleConfig, due_slot_count, resolve_slots
from opensocial.core.settings import Settings, resolve_schedule_tz
from opensocial.publish.base import Publisher, estimate_cost, get_publisher

# Statuses a post can be in and still be eligible to publish. With the approval
# queue retired this is just "draft" — published/rejected/failed are terminal.
ELIGIBLE_STATUSES = ("draft",)

# How recently a slot must have come due to still publish. This is jitter
# tolerance so a slot is caught on the tick(s) right after its time, NOT a
# catch-up window — slots older than this are treated as missed and skipped, so
# enabling autopilot mid-day never replays a backlog.
SLOT_GRACE = timedelta(minutes=2)


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
    niche_slug: str,
    config: dict,
    *,
    platform_account_id: str | None = None,
) -> GeneratedPost | None:
    """Highest-priority eligible draft for the niche.

    Volume is bounded by the global daily cap and minimum gap in
    :func:`run_due_slots`, not by per-tone caps (which no longer exist).

    ``platform_account_id`` restricts to drafts generated by one workspace.
    Niches are a shared catalog, so two workspaces can hold drafts for the same
    niche; without this scope a workspace could publish another's draft.
    """
    stmt = select(GeneratedPost).where(
        GeneratedPost.niche_slug == niche_slug,
        GeneratedPost.status.in_(ELIGIBLE_STATUSES),
    )
    if platform_account_id is not None:
        stmt = stmt.where(GeneratedPost.platform_account_id == platform_account_id)
    return session.execute(
        stmt.order_by(
            GeneratedPost.priority_score.is_(None),  # non-null scores first
            GeneratedPost.priority_score.desc(),
            GeneratedPost.created_at.asc(),
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


def run_due_slots(
    session: Session,
    niche_slug: str,
    config: dict,
    settings: Settings,
    *,
    now: datetime | None = None,
    publisher: Publisher | None = None,
    credentials: dict | None = None,
    account: PlatformAccount | None = None,
) -> list[PublishOutcome]:
    """Publish the posts that just came due for a niche at ``now``.

    No-op in ``manual`` app mode. Otherwise the target is the number of slots
    whose time fell within ``SLOT_GRACE`` of ``now`` — missed slots from earlier
    are skipped, not caught up — clamped by the account's per-account daily cap
    and the minimum gap.

    The niche's owning account is resolved from ``config`` (or passed in). When
    a niche has no resolvable account *and* no explicit publisher/credentials is
    injected, the niche is held (logged) so nothing posts to the wrong account.
    An explicit ``publisher``/``credentials`` (e.g. tests) bypasses resolution.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    if settings.app_mode != "auto":
        return []  # manual mode never auto-publishes

    sched = ScheduleConfig.from_niche(config)
    slots = resolve_slots(sched, niche_slug, now, tz=resolve_schedule_tz(session))
    target = due_slot_count(slots, now, SLOT_GRACE)
    if target == 0:
        return []

    # Resolve the posting account unless creds/publisher were injected directly.
    injected = publisher is not None or credentials is not None
    if account is None and not injected:
        account = resolve_account_for_niche(session, config)
        # Hold only for *live* posting — dry-run still simulates (account_id
        # stays None) so the dashboard can preview before any account is set.
        if account is None and not settings.dry_run:
            log(
                session, "warn",
                f"[{niche_slug}] no account assigned — holding posts",
            )
            session.commit()
            return []
    account_id = account.id if account is not None else None
    if credentials is None and account is not None:
        credentials = load_credentials_for_account(session, settings, account_id)

    # Minimum gap since the last successful publish (scoped to this workspace).
    last = last_published_at(session, niche_slug, platform_account_id=account_id)
    if last is not None:
        last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
        if now - last < timedelta(minutes=sched.min_gap_minutes):
            return []

    cap = account.daily_post_cap if account is not None else None

    publisher = publisher or get_publisher(settings, credentials=credentials)
    outcomes: list[PublishOutcome] = []
    for _ in range(target):
        # Per-account daily cap (None = unlimited).
        if cap is not None and (
            published_today_count(
                session, platform_account_id=account_id, day=now
            )
            >= cap
        ):
            log(
                session, "warn",
                f"[{niche_slug}] account daily cap reached — holding posts",
            )
            session.commit()
            break
        post = select_post(
            session, niche_slug, config, platform_account_id=account_id
        )
        if post is None:
            break
        outcomes.append(
            publish_post(
                session, post, config, settings, publisher=publisher,
                credentials=credentials, platform_account_id=account_id,
            )
        )
    return outcomes
