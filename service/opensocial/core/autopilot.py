"""Autopilot: one post every X minutes per account, inside its window.

Each worker tick (every few seconds) does, for every workspace:

1. If its next post is due within ``FETCH_LEAD`` minutes, one **shared** fetch
   runs for all workspaces (throttled), so the post is written on fresh data.
2. When the post comes due (``ws:<id>:next_post_at``):

   * **auto mode**: publish a fresh pending draft if one exists (0 tokens),
     otherwise write one from the knowledge base and publish it. The daily cap
     and minimum gap still apply.
   * **manual mode**: write one draft into the review queue, but only while
     fewer than ``max_pending_drafts`` are waiting, so an ignored queue costs
     nothing.

3. Roll the next post time: one jittered gap later, inside the window.

The fetch → filter → knowledge-base ingest pipeline lives in
``core/commands._do_fetch``; drafting in ``core/generate.draft_next``.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session, sessionmaker

from opensocial.core.config import NicheConfig, load_all_niches
from opensocial.core.db import (
    GeneratedPost,
    PlatformAccount,
    get_app_setting,
    get_platform_account,
    last_published_for_account,
    list_platform_accounts,
    log,
    pending_draft_count,
    published_today_count,
    set_app_setting,
)
from opensocial.core.engine import (
    PublishOutcome,
    load_credentials_for_account,
    publish_post,
    select_post,
)
from opensocial.core.generate import DraftResult, draft_next
from opensocial.core.knowledge import PruneResult, prune
from opensocial.core.scheduler import (
    Cadence,
    first_post_time,
    get_next_post_at,
    in_window,
    load_cadence,
    next_post_time,
    set_next_post_at,
    window_bounds,
)
from opensocial.core.settings import (
    Settings,
    get_followed_niches,
    get_scoped_secret,
    get_voice_samples,
    load_ai_config,
    resolve_schedule_tz,
    resolve_settings,
)
from opensocial.publish.base import Publisher

# Fetch this long before a post is due, so it's written on fresh data.
FETCH_LEAD = timedelta(minutes=5)
# Never fetch more often than this, however many accounts post.
MIN_FETCH_INTERVAL = timedelta(minutes=10)
# Auto mode publishes a waiting draft only if it's at most this old.
FRESH_DRAFT_HOURS = 6
# After a failed draft (model down, bad key), retry this soon at most.
RETRY_AFTER = timedelta(minutes=15)

_LAST_FETCH_KEY = "kb_last_fetch"
_LAST_PRUNE_KEY = "kb_last_prune"
# How often the knowledge-base cleanup runs.
PRUNE_EVERY = timedelta(hours=1)


@dataclass
class WorkspaceContext:
    """Everything a workspace needs to draft and publish, resolved once."""

    workspace_id: str
    account: PlatformAccount
    settings: Settings
    cadence: Cadence
    niches: list[NicheConfig]
    ai: dict
    unsplash_key: str | None = None
    voice_samples: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.account.account_label


@dataclass
class TickOutcome:
    workspace_id: str
    # posted | drafted | held | skipped | failed | waiting | idle
    action: str
    detail: str = ""
    post_id: str | None = None
    next_post_at: datetime | None = None
    dry_run: bool = False


def ai_config_with_key(session: Session, workspace_id: str | None) -> dict:
    """A workspace's AI config with its own API key resolved into ``text``.

    AI keys are per workspace; this injects the decrypted key (the workspace's,
    else a legacy global default) so generation authenticates with the right
    one without relying on a single shared ``os.environ`` value.
    """
    from opensocial.ai.text import PROVIDER_KEY_ENV

    ai_cfg = load_ai_config(session, workspace_id)
    provider = ((ai_cfg.get("text") or {}).get("provider") or "").lower()
    env = PROVIDER_KEY_ENV.get(provider)
    if env:
        key = get_scoped_secret(session, workspace_id, env)
        if key:
            ai_cfg = {**ai_cfg, "text": {**ai_cfg["text"], "api_key": key}}
    return ai_cfg


def workspace_niches(
    session: Session, workspace_id: str | None, config_dir, slug: str | None = None
) -> list[NicheConfig]:
    """The workspace's active niches: followed and enabled (or just ``slug``)."""
    niches = load_all_niches(config_dir)
    if slug:
        return [n for n in niches if n.slug == slug]
    followed = set(get_followed_niches(session, workspace_id))
    return [n for n in niches if n.slug in followed and n.enabled]


def workspace_context(
    session: Session, workspace_id: str, config_dir, *, slug: str | None = None
) -> WorkspaceContext | None:
    account = get_platform_account(session, workspace_id)
    if account is None:
        return None
    samples, _ = get_voice_samples(session, workspace_id)
    return WorkspaceContext(
        workspace_id=workspace_id,
        account=account,
        settings=resolve_settings(session, workspace_id),
        cadence=load_cadence(session, workspace_id, config_dir=config_dir),
        niches=workspace_niches(session, workspace_id, config_dir, slug),
        ai=ai_config_with_key(session, workspace_id),
        unsplash_key=get_scoped_secret(session, workspace_id, "UNSPLASH_ACCESS_KEY"),
        voice_samples=samples,
    )


def draft_for_workspace(
    session: Session,
    ctx: WorkspaceContext,
    *,
    persist: bool = True,
    now: datetime | None = None,
    **kwargs,
) -> DraftResult | None:
    """Write the workspace's next post from the knowledge base."""
    result = draft_next(
        session, ctx.workspace_id, ctx.niches, ai_config=ctx.ai,
        unsplash_key=ctx.unsplash_key, voice_samples=ctx.voice_samples,
        persist=persist, now=now, **kwargs,
    )
    if result is not None and persist:
        how = {"score": "top ranked", "ai": "AI pick", "synthesis": "synthesis take"}[result.picked_by]
        src = f", {len(result.outlets)} outlet(s)" if result.outlets else ""
        art = ", full article" if result.used_article else ""
        log(
            session, "info",
            f"[{result.niche_slug}] drafted <{result.post_type}> for {ctx.label} "
            f"({how}{src}{art}): {result.title[:80]}",
        )
        session.commit()
    return result


def _daily_cap(ctx: WorkspaceContext) -> int | None:
    """The account's cap, with the global cap as a backstop (<=0 = none)."""
    caps = [c for c in (ctx.account.daily_post_cap, ctx.settings.global_daily_cap) if c and c > 0]
    return min(caps) if caps else None


def _niche_config(ctx: WorkspaceContext, slug: str) -> dict:
    for n in ctx.niches:
        if n.slug == slug:
            return n.raw
    return {}  # niche unfollowed since drafting: publish with defaults


def post_next(
    session: Session,
    ctx: WorkspaceContext,
    *,
    now: datetime | None = None,
    publisher: Publisher | None = None,
    **draft_kwargs,
) -> TickOutcome:
    """Publish the account's next post now: a fresh waiting draft, or a new one.

    Honors the daily cap and minimum gap. In live mode with no X credentials
    the post is held (rather than silently simulated).
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    wid = ctx.workspace_id

    cap = _daily_cap(ctx)
    if cap is not None and published_today_count(session, platform_account_id=wid, day=now) >= cap:
        return TickOutcome(wid, "held", f"daily cap of {cap} reached")
    last = last_published_for_account(session, wid)
    gap = timedelta(minutes=ctx.cadence.min_gap_minutes)
    if last is not None and now - last < gap:
        return TickOutcome(
            wid, "held", f"minimum gap of {ctx.cadence.min_gap_minutes} min",
            next_post_at=last + gap,
        )

    credentials = load_credentials_for_account(session, ctx.settings, wid)
    if publisher is None and not ctx.settings.dry_run and not credentials:
        return TickOutcome(wid, "held", "live mode but no X credentials saved")

    post = select_post(
        session, platform_account_id=wid,
        created_after=now - timedelta(hours=FRESH_DRAFT_HOURS),
    )
    if post is None:
        drafted = draft_for_workspace(session, ctx, now=now, **draft_kwargs)
        if drafted is None:
            return TickOutcome(wid, "skipped", "knowledge base is empty for the followed niches")
        post = session.get(GeneratedPost, drafted.post_id)

    outcome: PublishOutcome = publish_post(
        session, post, _niche_config(ctx, post.niche_slug), ctx.settings,
        publisher=publisher, credentials=credentials, platform_account_id=wid,
    )
    if not outcome.ok:
        return TickOutcome(wid, "failed", outcome.error or "publish failed", post_id=post.id)
    return TickOutcome(wid, "posted", post.text[:80], post_id=post.id, dry_run=outcome.dry_run)


def _draft_only(session: Session, ctx: WorkspaceContext, now: datetime, **kwargs) -> TickOutcome:
    wid = ctx.workspace_id
    waiting = pending_draft_count(session, wid)
    if waiting >= ctx.cadence.max_pending_drafts:
        return TickOutcome(wid, "skipped", f"{waiting} drafts already waiting for review")
    drafted = draft_for_workspace(session, ctx, now=now, **kwargs)
    if drafted is None:
        return TickOutcome(wid, "skipped", "knowledge base is empty for the followed niches")
    return TickOutcome(wid, "drafted", drafted.text[:80], post_id=drafted.post_id)


def wants_work(session: Session, ctx: WorkspaceContext) -> bool:
    """Whether this workspace's next due tick would draft or post anything."""
    if not ctx.niches:
        return False
    if ctx.settings.app_mode == "auto":
        return True
    return pending_draft_count(session, ctx.workspace_id) < ctx.cadence.max_pending_drafts


def schedule_status(
    session: Session, ctx: WorkspaceContext, *, now: datetime, next_at: datetime | None
) -> tuple[str, str]:
    """Whether the next scheduled post will actually happen, and if not, why.

    Returns ``(state, message)``; ``state`` is ``ok``, ``idle``, ``paused``
    (manual mode, review queue full), ``capped``, ``held`` (live mode, no X
    credentials), ``starting`` (no time rolled yet) or ``overdue`` (the time
    passed but no worker picked it up).
    """
    wid = ctx.workspace_id
    if not ctx.niches:
        return "idle", "Follow at least one niche on the Niches page to start posting."
    auto = ctx.settings.app_mode == "auto"
    if not auto:
        waiting = pending_draft_count(session, wid)
        if waiting >= ctx.cadence.max_pending_drafts:
            return "paused", (
                f"Drafting is paused: {waiting} drafts are waiting for review (limit "
                f"{ctx.cadence.max_pending_drafts}). Approve or reject one in the Queue and "
                "it picks up again at the next scheduled time."
            )
    cap = _daily_cap(ctx)
    if auto and cap is not None and published_today_count(
        session, platform_account_id=wid, day=now
    ) >= cap:
        return "capped", f"Daily cap of {cap} posts reached. Posting resumes tomorrow."
    if auto and not ctx.settings.dry_run and not load_credentials_for_account(
        session, ctx.settings, wid
    ):
        return "held", "Live mode, but this account has no X credentials saved. Posts are held."
    if next_at is None:
        return "starting", "The worker picks the next time on its next tick (is `serve` running?)."
    if now - next_at > timedelta(minutes=3):
        return "overdue", (
            "The scheduled time has passed and nothing ran. Is the worker (`serve`) running?"
        )
    return "ok", ""


def tick_workspace(
    session: Session,
    ctx: WorkspaceContext,
    *,
    now: datetime,
    tz=None,
    rng: random.Random | None = None,
    publisher: Publisher | None = None,
    **draft_kwargs,
) -> TickOutcome:
    """Advance one workspace's cadence; draft or post if its time has come."""
    rng = rng or random.Random()
    wid = ctx.workspace_id
    if not ctx.niches:
        return TickOutcome(wid, "idle", "no followed niches")

    next_at = get_next_post_at(session, wid)
    start, _ = window_bounds(ctx.cadence, now, tz)
    if next_at is None or next_at < start:
        # Never scheduled, or left over from an earlier day: roll a fresh time.
        next_at = first_post_time(ctx.cadence, now, tz, rng)
        set_next_post_at(session, wid, next_at)
        return TickOutcome(wid, "waiting", next_post_at=next_at)
    if now < next_at:
        return TickOutcome(wid, "waiting", next_post_at=next_at)
    if not in_window(ctx.cadence, now, tz):
        next_at = first_post_time(ctx.cadence, now, tz, rng)
        set_next_post_at(session, wid, next_at)
        return TickOutcome(wid, "waiting", next_post_at=next_at)

    try:
        if ctx.settings.app_mode == "auto":
            outcome = post_next(session, ctx, now=now, publisher=publisher, **draft_kwargs)
        else:
            outcome = _draft_only(session, ctx, now, **draft_kwargs)
    except Exception as exc:  # a model/network failure must not stall the loop
        session.rollback()
        outcome = TickOutcome(
            wid, "failed", f"{type(exc).__name__}: {exc}",
            next_post_at=now + min(RETRY_AFTER, timedelta(minutes=ctx.cadence.every_minutes)),
        )

    if outcome.next_post_at is None:
        if outcome.action == "held" and "daily cap" in outcome.detail:
            tomorrow_start, _ = window_bounds(ctx.cadence, now + timedelta(days=1), tz)
            outcome.next_post_at = first_post_time(ctx.cadence, tomorrow_start, tz, rng)
        else:
            outcome.next_post_at = next_post_time(ctx.cadence, now, tz, rng)
    set_next_post_at(session, wid, outcome.next_post_at)
    _log_outcome(session, ctx, outcome, tz)
    session.commit()
    return outcome


def _log_outcome(session: Session, ctx: WorkspaceContext, outcome: TickOutcome, tz) -> None:
    nxt = outcome.next_post_at.astimezone(tz).strftime("%H:%M") if outcome.next_post_at else "?"
    who = f"autopilot [{ctx.label}]"
    if outcome.action == "posted":
        verb = "would post (dry-run)" if outcome.dry_run else "posted"
        log(session, "info", f"{who} {verb}: {outcome.detail} · next {nxt}")
    elif outcome.action == "drafted":
        log(session, "info", f"{who} drafted for review · next {nxt}")
    elif outcome.action == "failed":
        log(session, "error", f"{who} failed: {outcome.detail} · retry {nxt}")
    elif outcome.action in ("held", "skipped"):
        log(session, "warn" if outcome.action == "held" else "info",
            f"{who} {outcome.action}: {outcome.detail} · next {nxt}")


def _fetch_due(session_factory: sessionmaker, now: datetime) -> bool:
    with session_factory() as s:
        raw = get_app_setting(s, _LAST_FETCH_KEY)
    if not raw:
        return True
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return True
    last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
    return now - last >= MIN_FETCH_INTERVAL


def record_fetch(session_factory: sessionmaker, now: datetime | None = None) -> None:
    """Note a fetch (shared by all accounts) so autopilot doesn't refetch soon."""
    with session_factory() as s:
        set_app_setting(s, _LAST_FETCH_KEY, (now or datetime.now(timezone.utc)).isoformat())


def maybe_prune(session_factory: sessionmaker, now: datetime) -> PruneResult | None:
    """Run the knowledge-base cleanup at most once per ``PRUNE_EVERY``."""
    with session_factory() as s:
        raw = get_app_setting(s, _LAST_PRUNE_KEY)
    if raw:
        try:
            last = datetime.fromisoformat(raw)
            last = last if last.tzinfo else last.replace(tzinfo=timezone.utc)
            if now - last < PRUNE_EVERY:
                return None
        except ValueError:
            pass
    with session_factory() as s:
        result = prune(s, now=now)
        set_app_setting(s, _LAST_PRUNE_KEY, now.isoformat())
        if result.total:
            log(
                s, "info",
                f"knowledge base cleanup: removed {result.stories} stale stor"
                f"{'y' if result.stories == 1 else 'ies'}, {result.items} old item(s), "
                f"{result.logs} log line(s), {result.commands} command(s)",
            )
            s.commit()
    return result


def tick_all(
    session_factory: sessionmaker,
    *,
    config_dir="config/niches",
    now: datetime | None = None,
    rng: random.Random | None = None,
    publisher: Publisher | None = None,
    fetch=None,
    **draft_kwargs,
) -> list[TickOutcome]:
    """One autopilot tick across every workspace (called by the worker loop).

    ``fetch(session_factory, config_dir)`` overrides the shared fetch (tests);
    by default it is the fetch → filter → ingest pipeline from
    ``core/commands``.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    with session_factory() as s:
        tz = resolve_schedule_tz(s)
        ids = [a.id for a in list_platform_accounts(s)]

    # 1. Shared fetch shortly before any account's next post.
    need_fetch = False
    with session_factory() as s:
        for wid in ids:
            ctx = workspace_context(s, wid, config_dir)
            if ctx is None or not wants_work(s, ctx):
                continue
            next_at = get_next_post_at(s, wid)
            if next_at is not None and next_at - now <= FETCH_LEAD and in_window(ctx.cadence, now, tz):
                need_fetch = True
                break
    if need_fetch and _fetch_due(session_factory, now):
        try:
            if fetch is None:
                from opensocial.core.commands import _do_fetch

                with session_factory() as s:
                    fetch_settings = resolve_settings(s, None)
                _do_fetch(session_factory, str(config_dir), None, fetch_settings)
            else:
                fetch(session_factory, config_dir)
        except Exception as exc:
            with session_factory() as s:
                log(s, "error", f"autopilot fetch failed: {exc}")
                s.commit()
        record_fetch(session_factory, now)

    # 2. Forget stale knowledge (hourly), so the database stops growing.
    maybe_prune(session_factory, now)

    # 3. Each workspace advances its own cadence.
    outcomes: list[TickOutcome] = []
    for wid in ids:
        with session_factory() as s:
            ctx = workspace_context(s, wid, config_dir)
            if ctx is None:
                continue
            outcomes.append(
                tick_workspace(s, ctx, now=now, tz=tz, rng=rng, publisher=publisher, **draft_kwargs)
            )
    return outcomes
