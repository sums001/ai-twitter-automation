"""HTTP API for the dashboard (FastAPI).

The dashboard is a browser app, so the "shared SQLite" bridge gets a thin HTTP
layer: reads query the DB directly, slow actions (fetch/generate/publish) go
through the ``commands`` table and are executed by the background worker that
``opensocial serve`` runs alongside this app. Fast state transitions
(approve/reject/edit, toggles, config edits) are applied synchronously.

Secrets saved from the dashboard are Fernet-encrypted into ``app_settings`` as
``secret:<ENV_NAME>`` and injected into the process environment so sources,
and litellm pick them up without a restart.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func, select

from opensocial.core import approval as approval_logic
from opensocial.core.config import load_all_niches
from opensocial.core.db import (
    Command,
    ContentItemNiche,
    ContentItemRow,
    GeneratedPost,
    Log,
    PostHistory,
    add_platform_account,
    delete_platform_account,
    delete_workspace,
    ensure_default_workspace,
    enqueue_command,
    get_app_setting,
    get_platform_account,
    get_platform_account_by_label,
    list_platform_accounts,
    make_session_factory,
    published_today_count,
    reset_database,
    reset_workspace,
    set_app_setting,
    source_statuses,
    update_platform_account,
    upsert_source_status,
)
from opensocial.core.scheduler import (
    Cadence,
    get_next_post_at,
    load_cadence,
    projected_times,
    save_cadence,
    window_bounds,
)
from opensocial.core.settings import (
    get_followed_niches,
    get_schedule_timezone,
    get_voice_samples,
    get_scoped_setting,
    has_scoped_secret,
    load_ai_config,
    resolve_settings,
    resolve_tz,
    save_ai_config,
    set_followed_niches,
    set_schedule_timezone,
    set_scoped_secret,
    set_scoped_setting,
    set_voice_samples,
)
from opensocial.ai.text import AI_KEY_ENVS
from opensocial.ai.text import PROVIDERS as AI_PROVIDERS
from opensocial.sources import available_sources, get_source

# ---------------------------------------------------------------------------
# Source metadata for the dashboard
# ---------------------------------------------------------------------------
# Every source describes itself (label, auth, key envs, origin spec) on its
# plugin class — see ``opensocial.sources.base.Source``. Nothing here repeats it.


def _origin_source(name: str):
    """The plugin class for ``name`` if it takes user-added origins, else None."""
    cls = get_source(name)
    return cls if cls.origin_key else None


# Credential groups for the Settings → Credentials tab. X is special-cased
# (stored as an encrypted platform account, not env secrets). Source keys are
# derived from the registry, so adding a keyed source needs no change here.
CREDENTIAL_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    *(
        (spec["label"], (spec["key_env"],))
        for spec in AI_PROVIDERS.values()
        if spec["key_env"]
    ),
    *(
        (get_source(n).label or n, get_source(n).key_envs)
        for n in available_sources()
        if get_source(n).key_envs
    ),
    ("Unsplash", ("UNSPLASH_ACCESS_KEY",)),
]

ALLOWED_SECRET_ENVS = {env for _, envs in CREDENTIAL_GROUPS for env in envs}

# Per-workspace credential keys (stored under ``ws:<id>:secret:<ENV>``, never
# injected into ``os.environ``) whose set/not-set status the dashboard surfaces
# via ``/api/ai``. AI keys (one per provider in ``ai.text.PROVIDERS``)
# authenticate text generation; Unsplash authenticates image lookups for niches
# whose image source is Unsplash. The *source* API keys (YouTube, Guardian…)
# stay global.
WORKSPACE_KEY_ENVS = (*AI_KEY_ENVS, "UNSPLASH_ACCESS_KEY")

COMMAND_TYPES = {
    "fetch_sources",
    "generate_posts",
    "run_slots",
    "post_now",
    "regenerate_post",
}

_NICHE_PREFIX = re.compile(r"^\[([a-z0-9_-]+)\]\s*", re.I)


def _story_ttl(session) -> int:
    from opensocial.core.knowledge import story_ttl_hours

    return story_ttl_hours(session)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _today_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    ref = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def inject_stored_secrets(session_factory) -> int:
    """Decrypt every ``secret:*`` app setting into ``os.environ``.

    Env vars already set by the operator are never overwritten. Returns how
    many were injected.
    """
    from opensocial.core.db import AppSetting
    from opensocial.core.secrets import SecretsError, decrypt_credentials

    injected = 0
    with session_factory() as s:
        settings = resolve_settings(s)
        rows = s.execute(
            select(AppSetting).where(AppSetting.key.like("secret:%"))
        ).scalars()
        for row in rows:
            env_name = row.key.split(":", 1)[1]
            if os.environ.get(env_name):
                continue
            try:
                value = decrypt_credentials(
                    row.value.encode("ascii"), settings.secret_key
                ).get("value")
            except SecretsError:
                continue
            if value:
                os.environ[env_name] = value
                injected += 1
    return injected


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class SettingsPatch(BaseModel):
    dry_run: bool | None = None
    app_mode: str | None = None
    global_daily_cap: int | None = None  # install-wide backstop (0 = none)
    max_post_attempts: int | None = None  # publish retries before a draft fails
    story_ttl_hours: int | None = None  # forget unused stories after this long


class ResetIn(BaseModel):
    confirm: bool = False
    clear_credentials: bool = False


class CommandIn(BaseModel):
    type: str
    payload: dict | None = None


class EditIn(BaseModel):
    text: str


class SourcePatch(BaseModel):
    enabled: bool | None = None


class OriginIn(BaseModel):
    niche: str
    url: str | None = None  # POST: the origin URL to parse
    value: str | None = None  # DELETE: the parsed value to remove


class OriginEditIn(BaseModel):
    niche: str  # the origin's current niche
    value: str  # the origin's current parsed value (being edited)
    url: str  # the new origin URL (re-parsed)
    new_niche: str | None = None  # optional: move the origin to another niche


class AiConfigIn(BaseModel):
    text: dict | None = None


class FollowedIn(BaseModel):
    slugs: list[str]


class SecretIn(BaseModel):
    key: str
    value: str


class XAccountIn(BaseModel):
    label: str = "default"
    api_key: str
    api_secret: str
    access_token: str
    access_token_secret: str
    daily_post_cap: int | None = None


class WorkspaceIn(BaseModel):
    label: str  # the workspace name
    daily_post_cap: int | None = None


class AccountPatch(BaseModel):
    label: str | None = None
    daily_post_cap: int | None = None
    clear_cap: bool = False  # set daily_post_cap back to unlimited


class CadencePut(BaseModel):
    window: list[str]  # ["HH:MM", "HH:MM"]: the account's daily posting window
    every_minutes: int  # one post roughly this often inside the window
    jitter_pct: int = 20  # each gap randomized by ± this percent
    min_gap_minutes: int = 20  # safety: never two posts closer than this
    max_pending_drafts: int | None = None  # ignored (removed); accepted for old clients


class VoiceIn(BaseModel):
    samples: list[str]


class TimezonePut(BaseModel):
    timezone: str = ""  # IANA name (e.g. "America/New_York"); "" = server-local


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    db_path: str | Path = "opensocial.db",
    config_dir: str | Path = "config/niches",
    *,
    seed_default: bool = True,
) -> FastAPI:
    session_factory = make_session_factory(db_path)
    config_dir = Path(config_dir)
    # Keep the fallback encryption keyfile next to the DB.
    os.environ.setdefault(
        "OPENSOCIAL_KEYFILE", str(Path(db_path).resolve().parent / "opensocial.key")
    )
    # The dashboard always operates inside a workspace, so guarantee one exists.
    if seed_default:
        with session_factory() as s:
            ensure_default_workspace(s)

    app = FastAPI(title="OpenX API")
    app.state.session_factory = session_factory
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def niches():
        return load_all_niches(config_dir)

    def niche_or_404(slug: str):
        path = config_dir / f"{slug}.json"
        if not path.exists():
            raise HTTPException(404, f"no niche config for {slug!r}")
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), path

    def write_niche(path: Path, data: dict) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    def secret_key_or_400(session) -> str:
        from opensocial.core.secrets import keyfile_secret

        settings = resolve_settings(session)
        if settings.secret_key:
            return settings.secret_key
        # First secret saved from the dashboard: create the local keyfile.
        return keyfile_secret(create=True)

    def account_niche_slugs(account_id: str | None) -> list[str] | None:
        """The niches a workspace *works in* — its followed set — or ``None``.

        Niches are a shared catalog: any workspace may select (follow) any
        niche, and two workspaces following the same niche generate
        independently (per-workspace AI config + account). A workspace's active
        niches are therefore exactly the ones it has selected, not a private
        slice of the catalog. ``None``/empty ``account_id`` means "no scoping".

        This scopes *config-level* views (schedule, logs, the overview funnel).
        Per-workspace *draft/post* data is isolated by ``platform_account_id``
        instead, so the same niche followed by two workspaces never leaks drafts
        between them.
        """
        if not account_id:
            return None
        with session_factory() as s:
            return list(get_followed_niches(s, account_id))

    # ---- status & settings ------------------------------------------------

    @app.get("/api/status")
    def status(account: str | None = None):
        with session_factory() as s:
            settings = resolve_settings(s, account)
            published = published_today_count(s, platform_account_id=account) \
                if account else published_today_count(s)
            # Drafts belong to the workspace that generated them, isolated by
            # platform_account_id (niches are shared across workspaces).
            q = select(func.count()).select_from(GeneratedPost).where(
                GeneratedPost.status == "draft"
            )
            if account:
                q = q.where(GeneratedPost.platform_account_id == account)
            queue_pending = s.execute(q).scalar()
            ttl = _story_ttl(s)
        return {
            "dry_run": settings.dry_run,
            "app_mode": settings.app_mode,
            "global_daily_cap": settings.global_daily_cap,
            "story_ttl_hours": ttl,
            "max_post_attempts": settings.max_post_attempts,
            "secret_key_set": bool(settings.secret_key),
            "published_today": published,
            "queue_pending": int(queue_pending or 0),
        }

    @app.patch("/api/settings")
    def patch_settings(patch: SettingsPatch, account: str | None = None):
        # dry_run / app_mode are per workspace (the cadence is on /api/schedule).
        with session_factory() as s:
            if patch.dry_run is not None:
                set_scoped_setting(s, account, "dry_run", "true" if patch.dry_run else "false")
            if patch.story_ttl_hours is not None:
                from opensocial.core.knowledge import set_story_ttl_hours

                try:
                    set_story_ttl_hours(s, patch.story_ttl_hours)
                except ValueError as e:
                    raise HTTPException(422, str(e))
            if patch.app_mode is not None:
                if patch.app_mode not in ("manual", "auto"):
                    raise HTTPException(422, "app_mode must be 'manual' or 'auto'")
                set_scoped_setting(s, account, "app_mode", patch.app_mode)
            # Install-wide limits (not per workspace).
            if patch.global_daily_cap is not None:
                if not 0 <= patch.global_daily_cap <= 1000:
                    raise HTTPException(422, "global_daily_cap must be between 0 and 1000")
                set_app_setting(s, "global_daily_cap", str(patch.global_daily_cap))
            if patch.max_post_attempts is not None:
                if not 1 <= patch.max_post_attempts <= 10:
                    raise HTTPException(422, "max_post_attempts must be between 1 and 10")
                set_app_setting(s, "max_post_attempts", str(patch.max_post_attempts))
        return status(account)

    @app.post("/api/reset")
    def reset(body: ResetIn):
        """Wipe runtime data (and optionally credentials). Config files survive."""
        if not body.confirm:
            raise HTTPException(422, "confirm must be true to reset the database")
        with session_factory() as s:
            deleted = reset_database(
                s, clear_credentials=body.clear_credentials, config_dir=config_dir
            )
        # Clearing credentials wipes every workspace; re-seed the default one so
        # the app always has a workspace to operate in (same guarantee as at
        # startup). Idempotent when an account survived the reset.
        with session_factory() as s:
            ensure_default_workspace(s)
        # Drop in-process secrets too so cleared keys stop being used immediately.
        if body.clear_credentials:
            for env in ALLOWED_SECRET_ENVS:
                os.environ.pop(env, None)
        return {"ok": True, "cleared_credentials": body.clear_credentials, "deleted": deleted}

    # ---- overview ----------------------------------------------------------

    @app.get("/api/overview")
    def overview(account: str | None = None):
        start, end = _today_bounds()
        with session_factory() as s:
            # Published-per-niche is this workspace's own publishes (isolated by
            # platform_account_id); content found/candidates are shared (one
            # fetch pass serves every workspace) so they stay global per niche.
            pub_stmt = (
                select(GeneratedPost.niche_slug, func.count())
                .join(PostHistory, PostHistory.generated_post_id == GeneratedPost.id)
                .where(
                    PostHistory.status == "success",
                    PostHistory.attempted_at >= start,
                    PostHistory.attempted_at < end,
                )
                .group_by(GeneratedPost.niche_slug)
            )
            if account:
                pub_stmt = pub_stmt.where(
                    PostHistory.platform_account_id == account
                )
            published_by_niche = dict(s.execute(pub_stmt).all())
            found_by_niche = dict(
                s.execute(
                    select(ContentItemNiche.niche_slug, func.count())
                    .join(
                        ContentItemRow,
                        ContentItemRow.id == ContentItemNiche.content_item_id,
                    )
                    .where(ContentItemRow.fetched_at >= start)
                    .group_by(ContentItemNiche.niche_slug)
                ).all()
            )
            cand_by_niche = dict(
                s.execute(
                    select(ContentItemNiche.niche_slug, func.count())
                    .join(
                        ContentItemRow,
                        ContentItemRow.id == ContentItemNiche.content_item_id,
                    )
                    .where(
                        ContentItemRow.fetched_at >= start,
                        ContentItemNiche.status == "candidate",
                    )
                    .group_by(ContentItemNiche.niche_slug)
                ).all()
            )
            spend_stmt = select(
                func.coalesce(func.sum(PostHistory.cost_estimate), 0.0)
            ).where(
                PostHistory.attempted_at >= start,
                PostHistory.attempted_at < end,
            )
            if account:
                spend_stmt = spend_stmt.where(
                    PostHistory.platform_account_id == account
                )
            spend = s.execute(spend_stmt).scalar()
            link_stmt = select(func.count()).select_from(PostHistory).where(
                PostHistory.attempted_at >= start,
                PostHistory.attempted_at < end,
                PostHistory.included_source_link.is_(True),
                PostHistory.status == "success",
            )
            if account:
                link_stmt = link_stmt.where(
                    PostHistory.platform_account_id == account
                )
            with_link = s.execute(link_stmt).scalar()
            eligible_stmt = select(func.count()).select_from(GeneratedPost).where(
                GeneratedPost.status == "draft"
            )
            if account:
                eligible_stmt = eligible_stmt.where(
                    GeneratedPost.platform_account_id == account
                )
            eligible = s.execute(eligible_stmt).scalar()
            logs = list(
                s.execute(
                    select(Log).order_by(Log.id.desc()).limit(10)
                ).scalars()
            )
            statuses = source_statuses(s)
            settings = resolve_settings(s, account)
            selected = set(get_followed_niches(s, account))

        all_niches = niches()
        # Total = every installed source plugin; active = those explicitly
        # enabled (sources start disabled on a fresh DB — the user enables them).
        all_source_names = available_sources()
        enabled_sources = sum(
            1
            for name in all_source_names
            if name in statuses and statuses[name].enabled_globally
        )

        funnel = []
        for n in all_niches:
            # Show a niche only if it's selected AND has at least one source
            # that is enabled for the niche AND globally enabled.
            has_enabled_source = any(
                scfg.get("enabled") is not False
                and (name in statuses and statuses[name].enabled_globally)
                for name, scfg in n.sources.items()
            )
            if not (n.slug in selected and has_enabled_source):
                continue
            funnel.append(
                {
                    "slug": n.slug,
                    "display_name": n.display_name,
                    "enabled": n.enabled,
                    "selected": True,
                    "found": found_by_niche.get(n.slug, 0),
                    "candidates": cand_by_niche.get(n.slug, 0),
                    "published": published_by_niche.get(n.slug, 0),
                }
            )

        activity = []
        for row in logs:
            m = _NICHE_PREFIX.match(row.message)
            activity.append(
                {
                    "id": row.id,
                    "ts": _iso(row.logged_at),
                    "level": row.level,
                    "msg": row.message,
                    "niche": m.group(1) if m else None,
                }
            )

        # published_by_niche is already scoped to this workspace's publishes.
        published_today = sum(published_by_niche.values())
        return {
            "published_today": published_today,
            "global_daily_cap": settings.global_daily_cap,
            "pending_total": int(eligible or 0),
            "sources_active": enabled_sources,
            "sources_total": len(all_source_names),
            "spend_today": round(float(spend or 0.0), 4),
            "published_with_link": int(with_link or 0),
            "funnel": funnel,
            "activity": activity,
            # First-run onboarding: the pipeline can't do anything until the
            # user has both enabled a source and selected a niche.
            "setup": {
                "selected_niches": len(selected),
                "enabled_sources": enabled_sources,
                "needs_setup": len(selected) == 0 or enabled_sources == 0,
            },
        }

    @app.get("/api/dashboard")
    def dashboard_stats(account: str | None = None, days: int = 14):
        """Totals, daily series and breakdowns for the dashboard's charts.

        Stories and content are shared across workspaces (one fetch serves all),
        so their counts are global; drafts and publishes are scoped to
        ``account``. Daily buckets are UTC dates, oldest first.
        """
        from opensocial.core.db import Story
        from opensocial.core.knowledge import shortlist

        days = max(1, min(days, 90))
        today_start, _ = _today_bounds()
        since = today_start - timedelta(days=days - 1)
        week_ago = today_start - timedelta(days=6)

        def by_day(col, stmt):
            day = func.date(col)
            rows = s.execute(stmt.add_columns(day, func.count()).where(col >= since).group_by(day)).all()
            return {str(d): int(n) for d, n in rows}

        def scoped(stmt, model):
            return stmt.where(model.platform_account_id == account) if account else stmt

        def count(stmt):
            return int(s.execute(stmt).scalar() or 0)

        with session_factory() as s:
            selected = get_followed_niches(s, account)

            items_day = by_day(ContentItemRow.fetched_at, select())
            stories_day = by_day(Story.first_seen_at, select())
            drafts_day = by_day(
                GeneratedPost.created_at, scoped(select(), GeneratedPost)
            )
            pub_day = by_day(
                PostHistory.attempted_at,
                scoped(select().where(PostHistory.status == "success"), PostHistory),
            )

            stories_total = count(select(func.count()).select_from(Story))
            items_total = count(select(func.count()).select_from(ContentItemRow))
            published_total = count(
                scoped(
                    select(func.count()).select_from(PostHistory).where(PostHistory.status == "success"),
                    PostHistory,
                )
            )
            status_rows = s.execute(
                scoped(select(GeneratedPost.status, func.count()), GeneratedPost).group_by(GeneratedPost.status)
            ).all()

            by_source = [
                {"name": n, "items": int(c)}
                for n, c in s.execute(
                    select(ContentItemRow.source_name, func.count())
                    .where(ContentItemRow.fetched_at >= week_ago)
                    .group_by(ContentItemRow.source_name)
                    .order_by(func.count().desc())
                ).all()
            ]

            # Per-niche, last 7 days.
            niche_items = dict(
                s.execute(
                    select(ContentItemNiche.niche_slug, func.count())
                    .join(ContentItemRow, ContentItemRow.id == ContentItemNiche.content_item_id)
                    .where(ContentItemRow.fetched_at >= week_ago)
                    .group_by(ContentItemNiche.niche_slug)
                ).all()
            )
            niche_drafts = dict(
                s.execute(
                    scoped(select(GeneratedPost.niche_slug, func.count()), GeneratedPost)
                    .where(GeneratedPost.created_at >= week_ago)
                    .group_by(GeneratedPost.niche_slug)
                ).all()
            )
            niche_pub = dict(
                s.execute(
                    scoped(
                        select(GeneratedPost.niche_slug, func.count())
                        .join(PostHistory, PostHistory.generated_post_id == GeneratedPost.id)
                        .where(PostHistory.status == "success", PostHistory.attempted_at >= week_ago),
                        PostHistory,
                    ).group_by(GeneratedPost.niche_slug)
                ).all()
            )
            niche_stories: dict[str, int] = {}
            for rel in s.execute(
                select(Story.niche_relevance).where(Story.first_seen_at >= week_ago)
            ).scalars():
                for slug in (rel or {}):
                    niche_stories[slug] = niche_stories.get(slug, 0) + 1

            top = []
            if selected:
                try:
                    ranked = shortlist(s, account, selected, k=6)
                except Exception:  # knowledge base unavailable → just omit the list
                    ranked = []
                top = [
                    {
                        "id": sc.story.id,
                        "title": sc.story.title,
                        "niche": sc.niche_slug,
                        "outlets": len(sc.story.outlets or []),
                        "items": sc.story.item_count,
                        "first_seen": _iso(sc.story.first_seen_at),
                        "score": round(sc.score, 3),
                    }
                    for sc in ranked
                ]

        names = {n.slug: n.display_name for n in niches()}
        by_niche = [
            {
                "slug": slug,
                "display_name": names.get(slug, slug),
                "stories": niche_stories.get(slug, 0),
                "items": int(niche_items.get(slug, 0)),
                "drafts": int(niche_drafts.get(slug, 0)),
                "published": int(niche_pub.get(slug, 0)),
            }
            for slug in selected
        ]
        by_niche.sort(key=lambda r: (-r["stories"], -r["items"]))

        daily = []
        for i in range(days):
            d = (since + timedelta(days=i)).date().isoformat()
            daily.append(
                {
                    "date": d,
                    "items": items_day.get(d, 0),
                    "stories": stories_day.get(d, 0),
                    "drafts": drafts_day.get(d, 0),
                    "published": pub_day.get(d, 0),
                }
            )
        today = daily[-1]
        return {
            "totals": {
                "stories": stories_total,
                "stories_today": today["stories"],
                "items": items_total,
                "items_today": today["items"],
                "published": published_total,
                "published_today": today["published"],
                "drafts_today": today["drafts"],
            },
            "daily": daily,
            "post_status": {str(k): int(v) for k, v in status_rows},
            "by_source": by_source,
            "by_niche": by_niche,
            "top_stories": top,
        }

    # ---- niches ------------------------------------------------------------

    @app.get("/api/niches")
    def list_niches(account: str | None = None):
        with session_factory() as s:
            followed = set(get_followed_niches(s, account))
            statuses = source_statuses(s)

        def has_enabled_source(n) -> bool:
            # At least one source enabled for the niche AND globally enabled.
            return any(
                scfg.get("enabled") is not False
                and (name in statuses and statuses[name].enabled_globally)
                for name, scfg in n.sources.items()
            )

        return [
            {
                "slug": n.slug,
                "display_name": n.display_name,
                "enabled": n.enabled,
                "followed": n.slug in followed,
                "has_enabled_source": has_enabled_source(n),
                "account_id": n.account_id,
            }
            # Niches are a shared catalog — every workspace sees all of them.
            # ``followed`` is per-workspace, so selection still differs per
            # workspace even though the catalog is common.
            for n in niches()
        ]

    # Declared before ``/api/niches/{slug}`` so the path param doesn't match it.
    @app.get("/api/niches/followed")
    def get_followed(account: str | None = None):
        with session_factory() as s:
            return {"followed": get_followed_niches(s, account)}

    @app.put("/api/niches/followed")
    def put_followed(body: FollowedIn, account: str | None = None):
        valid = {n.slug for n in niches()}
        unknown = [s for s in body.slugs if s not in valid]
        if unknown:
            raise HTTPException(422, f"unknown niche slug(s): {', '.join(unknown)}")
        with session_factory() as s:
            saved = set_followed_niches(s, body.slugs, account)
        # Niches are a shared catalog: selecting one only updates this
        # workspace's followed list (namespaced per workspace). No ownership is
        # claimed, so multiple workspaces can follow the same niche and each
        # generates independently (own AI config + posting account).
        return {"followed": saved}

    @app.get("/api/niches/{slug}")
    def get_niche(slug: str):
        data, _ = niche_or_404(slug)
        return data

    @app.put("/api/niches/{slug}")
    def put_niche(slug: str, config: dict):
        data, path = niche_or_404(slug)
        if config.get("slug") != slug:
            raise HTTPException(422, "config slug must match the URL slug")
        write_niche(path, config)
        return config

    # ---- schedule (per-account cadence) ---------------------------------------

    @app.get("/api/schedule/timezone")
    def get_timezone():
        """The schedule timezone (IANA name; "" means the server's local zone)."""
        with session_factory() as s:
            return {"timezone": get_schedule_timezone(s)}

    @app.put("/api/schedule/timezone")
    def put_timezone(body: TimezonePut):
        """Set the timezone posting windows are interpreted/displayed in."""
        with session_factory() as s:
            try:
                tz = set_schedule_timezone(s, body.timezone)
            except ValueError as e:
                raise HTTPException(422, str(e))
            s.commit()
        return {"timezone": tz}

    def _today_activity(s, account: str | None, start, end) -> list[dict]:
        stmt = (
            select(GeneratedPost, PostHistory.attempted_at)
            .join(PostHistory, PostHistory.generated_post_id == GeneratedPost.id)
            .where(
                PostHistory.status == "success",
                PostHistory.attempted_at >= start,
                PostHistory.attempted_at < end,
            )
            .order_by(PostHistory.attempted_at)
        )
        if account:
            stmt = stmt.where(PostHistory.platform_account_id == account)
        return [
            {
                "at": _iso(at),
                "niche": post.niche_slug,
                "type": post.post_type,
                "text": post.text,
                "post_id": post.id,
            }
            for post, at in s.execute(stmt).all()
        ]

    @app.get("/api/schedule")
    def schedule(account: str | None = None):
        """The account's cadence, next post time and today's posting timeline."""
        now = datetime.now(timezone.utc)
        with session_factory() as s:
            tz_name = get_schedule_timezone(s)
            tz = resolve_tz(tz_name)
            cadence = load_cadence(s, account, config_dir=config_dir)
            settings = resolve_settings(s, account)
            next_at = get_next_post_at(s, account) if account else None
            win_start, win_end = window_bounds(cadence, now, tz)
            day_start = win_start.replace(hour=0, minute=0).astimezone(timezone.utc)
            day_end = day_start + timedelta(days=1)
            posted = _today_activity(s, account, day_start, day_end)
            pending_q = select(func.count()).select_from(GeneratedPost).where(
                GeneratedPost.status == "draft"
            )
            if account:
                pending_q = pending_q.where(GeneratedPost.platform_account_id == account)
            pending = int(s.execute(pending_q).scalar() or 0)
            acct = get_platform_account(s, account) if account else None
            followed = get_followed_niches(s, account) if account else []
            if next_at is not None and next_at < now - timedelta(hours=12):
                next_at = None  # left over from an earlier day; the worker re-rolls it
            state, message = "ok", ""
            if account:
                from opensocial.core.autopilot import schedule_status, workspace_context

                ctx = workspace_context(s, account, config_dir)
                if ctx is not None:
                    state, message = schedule_status(s, ctx, now=now, next_at=next_at)
        today = now.astimezone(tz).date()

        def when(t: datetime) -> dict:
            local = t.astimezone(tz)
            return {
                "at": _iso(t),
                "day": (local.date() - today).days,  # 0 = today, 1 = tomorrow
                "minute": local.hour * 60 + local.minute,  # minute of day in the schedule tz
            }

        upcoming = [
            when(t) for t in projected_times(cadence, next_at, now, tz)
            if t >= now - timedelta(minutes=1)
        ]
        caps = [c for c in ((acct.daily_post_cap if acct else None), settings.global_daily_cap) if c and c > 0]
        return {
            "now": _iso(now),
            "now_minute": now.astimezone(tz).hour * 60 + now.astimezone(tz).minute,
            "timezone": tz_name,
            "cadence": cadence.to_dict(),
            "mode": settings.app_mode,
            "dry_run": settings.dry_run,
            "next_post_at": _iso(next_at),
            "next_post": when(next_at) if next_at else None,
            "status": {"state": state, "message": message},
            "window_today": [_iso(win_start), _iso(win_end)],
            "upcoming": upcoming,
            "posted_today": [
                {**p, "minute": when(datetime.fromisoformat(p["at"]))["minute"]} for p in posted
            ],
            "pending_drafts": pending,
            "daily_cap": acct.daily_post_cap if acct is not None else None,
            "global_daily_cap": settings.global_daily_cap,
            "effective_cap": min(caps) if caps else None,
            "followed_niches": followed,
        }

    @app.put("/api/schedule")
    def put_schedule(body: CadencePut, account: str | None = None):
        """Save the account's window and rhythm; the next post time re-rolls."""
        win = body.window
        if len(win) != 2 or not all(re.fullmatch(r"\d{2}:\d{2}", w) for w in win):
            raise HTTPException(422, "window must be ['HH:MM','HH:MM']")
        cadence = Cadence(
            window=(win[0], win[1]),
            every_minutes=body.every_minutes,
            jitter_pct=body.jitter_pct,
            min_gap_minutes=body.min_gap_minutes,
        )
        with session_factory() as s:
            try:
                save_cadence(s, account, cadence)
            except ValueError as e:
                raise HTTPException(422, str(e))
        return schedule(account)

    # ---- voice samples -------------------------------------------------------

    @app.get("/api/voice")
    def get_voice(account: str | None = None):
        """The account's voice samples (real posts the writer imitates)."""
        with session_factory() as s:
            samples, is_default = get_voice_samples(s, account)
        return {"samples": samples, "default": is_default}

    @app.put("/api/voice")
    def put_voice(body: VoiceIn, account: str | None = None):
        with session_factory() as s:
            saved = set_voice_samples(s, account, body.samples)
        return {"samples": saved, "default": False}

    @app.delete("/api/voice")
    def reset_voice(account: str | None = None):
        """Forget the account's own samples and go back to the defaults."""
        from opensocial.core.db import AppSetting

        key = f"ws:{account}:voice_samples" if account else "voice_samples"
        with session_factory() as s:
            row = s.get(AppSetting, key)
            if row is not None:
                s.delete(row)
                s.commit()
        return get_voice(account)

    # ---- knowledge base ------------------------------------------------------

    @app.get("/api/stories")
    def stories(account: str | None = None, niche: str | None = None, limit: int = 40):
        """The knowledge base as this account sees it: the scored shortlist
        (unused stories, best first) plus recently used ones."""
        from opensocial.core.db import Story, StoryUsage
        from opensocial.core.knowledge import shortlist

        with session_factory() as s:
            slugs = [niche] if niche else (get_followed_niches(s, account) if account else [])
            if not slugs:
                slugs = [n.slug for n in niches()]
            ranked = shortlist(s, account, slugs, k=max(1, min(limit, 200)))
            used = []
            if account:
                rows = s.execute(
                    select(Story, StoryUsage)
                    .join(StoryUsage, StoryUsage.story_id == Story.id)
                    .where(
                        StoryUsage.platform_account_id == account,
                        StoryUsage.status == "used",
                    )
                    .order_by(StoryUsage.used_at.desc())
                    .limit(20)
                ).all()
                used = [
                    {
                        "id": st.id,
                        "title": st.title,
                        "outlets": st.outlets,
                        "used_at": _iso(u.used_at),
                        "post_id": u.generated_post_id,
                    }
                    for st, u in rows
                ]
            total = s.execute(select(func.count()).select_from(Story)).scalar() or 0
            return {
                "total_stories": int(total),
                "story_ttl_hours": _story_ttl(s),
                "niches": slugs,
                "shortlist": [
                    {
                        "id": sc.story.id,
                        "title": sc.story.title,
                        "summary": sc.story.summary,
                        "niche": sc.niche_slug,
                        "outlets": sc.story.outlets,
                        "items": sc.story.item_count,
                        "first_seen": _iso(sc.story.first_seen_at),
                        "score": sc.score,
                        "signals": {k: round(v, 3) for k, v in sc.signals.items()},
                    }
                    for sc in ranked
                ],
                "used": used,
            }

    # ---- posts (queue) -------------------------------------------------------

    @app.get("/api/posts")
    def posts(
        niche: str | None = None,
        status: str | None = None,
        account: str | None = None,
        limit: int = 200,
    ):
        stmt = (
            select(
                GeneratedPost, ContentItemRow.source_name, ContentItemRow.url
            )
            .join(
                ContentItemRow,
                GeneratedPost.content_item_id == ContentItemRow.id,
                isouter=True,
            )
            .order_by(GeneratedPost.created_at.desc())
            .limit(min(limit, 500))
        )
        if niche:
            stmt = stmt.where(GeneratedPost.niche_slug == niche)
        # Drafts are isolated per workspace by the generating account (niches
        # are shared, so two workspaces' drafts for the same niche must not mix).
        if account:
            stmt = stmt.where(GeneratedPost.platform_account_id == account)
        if status:
            stmt = stmt.where(GeneratedPost.status == status)
        with session_factory() as s:
            rows = s.execute(stmt).all()
            return [
                {
                    "id": p.id,
                    "niche": p.niche_slug,
                    "type": p.post_type,
                    "status": p.status,
                    "text": p.text,
                    "priority": p.priority_score,
                    "media_url": p.media_url,
                    "source": source_name,
                    "source_url": source_url,
                    "independent": p.content_item_id is None,
                    "attempts": p.post_attempts,
                    "error": p.post_error,
                    "created_at": _iso(p.created_at),
                    "scheduled_at": _iso(p.scheduled_at),
                }
                for p, source_name, source_url in rows
            ]

    def _post_or_404(s, post_id: str) -> GeneratedPost:
        post = s.get(GeneratedPost, post_id)
        if post is None:
            raise HTTPException(404, "post not found")
        return post

    @app.post("/api/posts/{post_id}/approve")
    def approve_post(post_id: str):
        with session_factory() as s:
            post = approval_logic.approve(s, _post_or_404(s, post_id))
            return {"id": post.id, "status": post.status}

    @app.post("/api/posts/{post_id}/reject")
    def reject_post(post_id: str):
        with session_factory() as s:
            post = approval_logic.reject(s, _post_or_404(s, post_id))
            return {"id": post.id, "status": post.status}

    @app.post("/api/posts/{post_id}/edit")
    def edit_post(post_id: str, body: EditIn):
        if not body.text.strip():
            raise HTTPException(422, "text must not be empty")
        with session_factory() as s:
            post = approval_logic.edit(s, _post_or_404(s, post_id), body.text.strip())
            return {"id": post.id, "status": post.status, "text": post.text}

    # ---- raw fetched content -------------------------------------------------

    @app.get("/api/content")
    def content(
        source: str | None = None,
        niche: str | None = None,
        q: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ):
        """Raw fetched ``content_items`` (newest first), filterable by source.

        Backs the dashboard's Raw Data tab: the unmodified item as it landed
        from the source plugin, including ``raw_metadata`` and ``engagement``.

        Returns ``{"total": N, "items": [...]}`` where ``total`` is the full
        count for the filter (ignoring paging) and ``items`` is the requested
        page, so the tab can paginate rather than silently truncate.
        """
        page = min(max(limit, 1), 500)
        start = max(offset, 0)

        def _apply_filters(stmt):
            if source:
                stmt = stmt.where(ContentItemRow.source_name == source)
            niche_slugs = [n for n in (niche or "").split(",") if n.strip()]
            if niche_slugs:
                stmt = stmt.join(
                    ContentItemNiche,
                    ContentItemNiche.content_item_id == ContentItemRow.id,
                ).where(ContentItemNiche.niche_slug.in_(niche_slugs))
            if q:
                stmt = stmt.where(ContentItemRow.title.ilike(f"%{q}%"))
            return stmt

        stmt = _apply_filters(
            select(ContentItemRow)
            .order_by(ContentItemRow.fetched_at.desc())
            .limit(page)
            .offset(start)
        )
        count_stmt = _apply_filters(
            select(func.count(func.distinct(ContentItemRow.id)))
        )
        with session_factory() as s:
            total = s.execute(count_stmt).scalar_one()
            rows = list(s.execute(stmt).scalars())
            ids = [r.id for r in rows]
            links: dict[str, list[dict]] = {}
            if ids:
                for cin in s.execute(
                    select(ContentItemNiche).where(
                        ContentItemNiche.content_item_id.in_(ids)
                    )
                ).scalars():
                    links.setdefault(cin.content_item_id, []).append(
                        {"niche": cin.niche_slug, "status": cin.status}
                    )
            items = [
                {
                    "id": r.id,
                    "source": r.source_name,
                    "category": r.source_category,
                    "title": r.title,
                    "url": r.url,
                    "author": r.author,
                    "summary": r.summary,
                    "body": r.body,
                    "published_at": _iso(r.published_at),
                    "fetched_at": _iso(r.fetched_at),
                    "media_urls": r.media_urls or [],
                    "tags": r.tags or [],
                    "language": r.language,
                    "sentiment": r.sentiment,
                    "engagement": r.engagement,
                    "raw_metadata": r.raw_metadata or {},
                    "niches": links.get(r.id, []),
                }
                for r in rows
            ]
            return {"total": total, "items": items}

    # ---- history -------------------------------------------------------------

    @app.get("/api/history")
    def history(
        days: int = 1,
        status: str | None = None,
        account: str | None = None,
        limit: int = 300,
    ):
        since = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        stmt = (
            select(PostHistory, GeneratedPost)
            .join(GeneratedPost, GeneratedPost.id == PostHistory.generated_post_id)
            .where(PostHistory.attempted_at >= since)
            .order_by(PostHistory.attempted_at.desc())
            .limit(min(limit, 1000))
        )
        if status:
            stmt = stmt.where(PostHistory.status == status)
        if account:
            # Scope on the account recorded at publish time (resilient to later
            # niche re-assignment).
            stmt = stmt.where(PostHistory.platform_account_id == account)
        with session_factory() as s:
            rows = s.execute(stmt).all()
            return [
                {
                    "id": h.id,
                    "ts": _iso(h.attempted_at),
                    "niche": p.niche_slug,
                    "type": p.post_type,
                    "status": h.status,
                    "cost": h.cost_estimate,
                    "link": h.included_source_link,
                    "url": h.platform_post_url,
                    # dry-run publishes record success with no platform post id
                    "dry": h.status == "success" and h.platform_post_id is None
                    and h.platform_post_url is None,
                    "error": h.error_message,
                }
                for h, p in rows
            ]

    # ---- logs ------------------------------------------------------------------

    @app.get("/api/logs")
    def logs(
        level: str | None = None,
        after_id: int = 0,
        limit: int = 300,
        account: str | None = None,
    ):
        # Scope to the workspace: keep lines whose ``[niche]`` prefix is in the
        # workspace's niches, plus prefix-less global service lines.
        scoped = account_niche_slugs(account)
        cap = min(limit, 1000)
        stmt = select(Log).order_by(Log.id.desc()).limit(cap if scoped is None else 1000)
        if level and level != "all":
            stmt = stmt.where(Log.level == level)
        if after_id:
            stmt = stmt.where(Log.id > after_id)
        with session_factory() as s:
            rows = list(s.execute(stmt).scalars())
        if scoped is not None:
            keep = set(scoped)

            def _visible(message: str) -> bool:
                m = _NICHE_PREFIX.match(message)
                return m.group(1) in keep if m else True

            rows = [r for r in rows if _visible(r.message)][:cap]
        rows.reverse()
        return [
            {
                "id": r.id,
                "ts": _iso(r.logged_at),
                "level": r.level,
                "msg": r.message,
            }
            for r in rows
        ]

    # ---- sources ------------------------------------------------------------------

    @app.get("/api/sources")
    def sources():
        all_niches = niches()
        names = available_sources()
        usage: dict[str, dict] = {}
        origins: dict[str, list] = {name: [] for name in names}
        for n in all_niches:
            for name, scfg in n.sources.items():
                if name not in origins:
                    continue  # stale niche entry for a removed source
                u = usage.setdefault(name, {"niches": [], "enabled_in": 0})
                u["niches"].append(n.slug)
                if scfg.get("enabled") is not False and n.enabled:
                    u["enabled_in"] += 1
                cls = _origin_source(name)
                if cls:
                    for value in scfg.get(cls.origin_key) or []:
                        origins[name].append(
                            {
                                "niche": n.slug,
                                "value": value,
                                "display": cls.origin_display(value),
                            }
                        )

        with session_factory() as s:
            statuses = source_statuses(s)

        out = []
        for name in names:
            cls = get_source(name)
            row = statuses.get(name)
            u = usage.get(name, {"niches": [], "enabled_in": 0})
            has_origins = bool(cls.origin_key)
            key_set = cls.key_available() if cls.key_envs else None
            out.append(
                {
                    **cls.describe(),
                    "kind": "dynamic" if has_origins else "static",
                    "enabled": row.enabled_globally if row is not None else False,
                    "last_fetch_at": _iso(row.last_fetch_at) if row is not None else None,
                    "last_status": row.last_fetch_status if row is not None else None,
                    "ok": (row.last_fetch_status or "ok").startswith("ok")
                    if row is not None
                    else True,
                    # back-compat: "required" | "optional" | None
                    "key": None if cls.auth == "none" else cls.auth,
                    "key_set": key_set,
                    "needs_key": cls.missing_required_key(),
                    "niches": u["niches"],
                    "enabled_in": u["enabled_in"],
                    "origins": origins[name] if has_origins else [],
                }
            )
        return out

    @app.patch("/api/sources/{name}")
    def patch_source(name: str, patch: SourcePatch):
        if name not in available_sources():
            raise HTTPException(404, f"unknown source {name!r}")
        if patch.enabled and get_source(name).upcoming:
            raise HTTPException(409, f"{name!r} is upcoming and can't be enabled yet")
        with session_factory() as s:
            row = upsert_source_status(s, name, enabled=patch.enabled)
            return {"id": name, "enabled": row.enabled_globally}

    def _origin_add(niche_slug: str, cls, value: str) -> None:
        """Append a parsed origin to a niche's source block (seeding it if new)."""
        data, path = niche_or_404(niche_slug)
        sources = data.setdefault("sources", {})
        block = sources.get(cls.name)
        if not isinstance(block, dict):
            block = dict(cls.origin_defaults)
            if any(st.name == "category" for st in cls.settings):
                block.setdefault("category", niche_slug)
            sources[cls.name] = block
        lst = block.setdefault(cls.origin_key, [])
        if value not in lst:
            lst.append(value)
        write_niche(path, data)

    def _origin_remove(niche_slug: str, cls, value: str) -> None:
        """Remove an origin from a niche, dropping the block once it's empty."""
        data, path = niche_or_404(niche_slug)
        block = (data.get("sources") or {}).get(cls.name)
        if not isinstance(block, dict):
            return
        lst = block.get(cls.origin_key) or []
        if value in lst:
            lst.remove(value)
        if lst:
            block[cls.origin_key] = lst
        else:
            data["sources"].pop(cls.name, None)
        write_niche(path, data)

    def _origin_source_or_404(name: str):
        if name not in available_sources():
            raise HTTPException(404, f"unknown source {name!r}")
        cls = _origin_source(name)
        if cls is None:
            raise HTTPException(404, f"{name!r} doesn't take user-added origins")
        return cls

    def _parse_origin_or_422(cls, url: str | None) -> str:
        if not url or not url.strip():
            raise HTTPException(422, "url is required")
        value = cls.parse_origin(url)
        if not value:
            raise HTTPException(422, f"could not parse a {cls.name} origin from {url!r}")
        return value

    def _origin_count(cls) -> int:
        """Total origins saved for a source across all niches."""
        return sum(
            len(scfg.get(cls.origin_key) or [])
            for n in niches()
            for sname, scfg in n.sources.items()
            if sname == cls.name
        )

    @app.post("/api/sources/{name}/origins")
    def add_origin(name: str, body: OriginIn):
        cls = _origin_source_or_404(name)
        value = _parse_origin_or_422(cls, body.url)
        if _origin_count(cls) >= cls.max_origins:
            raise HTTPException(422, f"{name} is capped at {cls.max_origins} origins")
        _origin_add(body.niche, cls, value)
        return {
            "name": name,
            "niche": body.niche,
            "value": value,
            "display": cls.origin_display(value),
        }

    @app.put("/api/sources/{name}/origins")
    def update_origin(name: str, body: OriginEditIn):
        """Edit a saved origin: re-parse the new url and (optionally) move niches."""
        cls = _origin_source_or_404(name)
        if not body.value:
            raise HTTPException(422, "value (the origin being edited) is required")
        new_value = _parse_origin_or_422(cls, body.url)
        target = body.new_niche or body.niche
        _origin_remove(body.niche, cls, body.value)
        _origin_add(target, cls, new_value)
        return {
            "name": name,
            "niche": target,
            "value": new_value,
            "display": cls.origin_display(new_value),
        }

    @app.delete("/api/sources/{name}/origins")
    def remove_origin(name: str, body: OriginIn):
        cls = _origin_source_or_404(name)
        if not body.value:
            raise HTTPException(422, "value is required")
        _origin_remove(body.niche, cls, body.value)
        return {"name": name, "niche": body.niche, "removed": body.value}

    # ---- per-workspace AI config (text); source/AI keys stay global ---------

    def _ai_key_status(s, account: str | None) -> dict:
        # Per-workspace keys (AI + Unsplash): present if the workspace has its
        # own (or a legacy global default), or the env var is set.
        return {
            env: has_scoped_secret(s, account, env) or bool(os.environ.get(env))
            for env in WORKSPACE_KEY_ENVS
        }

    @app.get("/api/ai/providers")
    def ai_providers():
        """The selectable text providers (``ai.text.PROVIDERS``), in order."""
        return [{"id": pid, **spec} for pid, spec in AI_PROVIDERS.items()]

    def _ai_text_error(text: dict) -> str | None:
        from opensocial.ai.text import canonical_provider, missing_fields

        pid = canonical_provider(text.get("provider"))
        spec = AI_PROVIDERS.get(pid)
        if spec is None:
            return f"unknown provider {text.get('provider')!r}"
        missing = missing_fields(pid, text)
        if missing:
            return f"{', '.join(missing)} is required for the {spec['label']} provider"
        if spec.get("endpoint_required") and not (text.get("endpoint") or "").strip():
            return f"endpoint is required for the {pid} provider"
        if spec.get("model_required") and not (text.get("model") or "").strip():
            return f"a model id is required for the {pid} provider"
        return None

    @app.get("/api/ai")
    def get_ai(account: str | None = None):
        with session_factory() as s:
            cfg = load_ai_config(s, account)
            # Surface which AI keys are present so the card can show set/not-set
            # without exposing the secrets themselves.
            cfg["key_status"] = _ai_key_status(s, account)
        return cfg

    @app.put("/api/ai")
    def put_ai(body: AiConfigIn, account: str | None = None):
        from opensocial.ai.text import canonical_provider, clean_field

        text = dict(body.text or {})
        error = _ai_text_error(text)
        if error:
            raise HTTPException(422, error)
        # Store provider fields cleaned (an account id pasted as a URL → the id).
        spec = AI_PROVIDERS.get(canonical_provider(text.get("provider"))) or {}
        for f in spec.get("fields", []):
            text[f["key"]] = clean_field(f["key"], text.get(f["key"]))
        with session_factory() as s:
            saved = save_ai_config(s, {"text": text}, account)
            saved["key_status"] = _ai_key_status(s, account)
        return saved

    @app.post("/api/ai/test")
    def test_ai(body: AiConfigIn, account: str | None = None):
        """Send a tiny prompt through the text provider and report the outcome.

        Tests the form's current (possibly unsaved) values, resolving the API key
        exactly as generation does: the workspace's stored key, else the env var.
        Always answers 200 with ``ok`` so the dashboard can show the error inline.
        """
        import time

        from opensocial.ai.text import (
            PROVIDER_KEY_ENV,
            TextProviderError,
            canonical_provider,
            get_text_provider,
            key_required,
            qualify_model,
        )
        from opensocial.core.settings import get_scoped_secret

        text = dict(body.text or {})
        provider_name = canonical_provider(text.get("provider"))
        error = _ai_text_error(text)
        if error:
            return {"ok": False, "error": error}

        env = PROVIDER_KEY_ENV.get(provider_name)
        if env:
            with session_factory() as s:
                key = get_scoped_secret(s, account, env) or os.environ.get(env)
            if key:
                text["api_key"] = key
            elif key_required(provider_name):
                return {"ok": False, "error": f"no API key set ({env})"}
        if text.get("model"):
            text["model"] = qualify_model(provider_name, text["model"])
        # Keep the probe cheap and bounded regardless of the saved settings.
        text["max_tokens"] = 16
        try:
            provider = get_text_provider({"ai": {"text": text}})
        except TextProviderError as exc:
            return {"ok": False, "error": str(exc)}
        if hasattr(provider, "params"):
            provider.params["timeout"] = 20

        started = time.monotonic()
        try:
            reply = provider.generate(
                "You are a connectivity check. Reply with the single word: OK",
                "ping",
            )
        except Exception as exc:  # any provider/network failure is the answer
            msg = (str(exc).strip().splitlines() or [type(exc).__name__])[0][:300]
            if "LLM Provider NOT provided" in str(exc):
                msg = (
                    f"model {text.get('model')!r} has no provider prefix — use e.g. "
                    "'ollama/<model>' for Ollama or 'openai/<model>' for an "
                    "OpenAI-compatible server"
                )
            elif provider_name == "local" and "404" in str(exc) and not text.get("endpoint", "").rstrip("/").endswith("/v1"):
                msg += " — OpenAI-compatible servers usually need '/v1' on the endpoint"
            return {"ok": False, "error": msg}
        return {
            "ok": True,
            "model": getattr(provider, "name", provider_name),
            "latency_ms": int((time.monotonic() - started) * 1000),
            "reply": (reply or "").strip()[:120],
        }

    # ---- commands --------------------------------------------------------------------

    @app.post("/api/commands")
    def post_command(cmd: CommandIn):
        if cmd.type not in COMMAND_TYPES:
            raise HTTPException(422, f"unknown command type {cmd.type!r}")
        with session_factory() as s:
            row = enqueue_command(s, cmd.type, cmd.payload or {})
            return {
                "id": row.id,
                "type": row.type,
                "status": row.status,
                "created_at": _iso(row.created_at),
            }

    @app.get("/api/commands")
    def get_commands(ids: str = "", limit: int = 40):
        with session_factory() as s:
            stmt = select(Command).order_by(Command.id.desc()).limit(min(limit, 200))
            if ids:
                try:
                    id_list = [int(i) for i in ids.split(",") if i.strip()]
                except ValueError:
                    raise HTTPException(422, "ids must be comma-separated integers")
                stmt = select(Command).where(Command.id.in_(id_list))
            rows = list(s.execute(stmt).scalars())
            return [
                {
                    "id": c.id,
                    "type": c.type,
                    "payload": c.payload,
                    "status": c.status,
                    "result": c.result,
                    "created_at": _iso(c.created_at),
                    "finished_at": _iso(c.finished_at),
                }
                for c in rows
            ]

    # ---- credentials --------------------------------------------------------------

    @app.get("/api/credentials")
    def credentials():
        with session_factory() as s:
            accounts = list_platform_accounts(s, platform="x")
        # "set" reflects whether any account actually has credentials enrolled —
        # not merely that an account row exists. The always-present default
        # workspace starts credential-less, so bool(accounts) would lie.
        has_creds = any(a.credentials_encrypted for a in accounts)
        groups = [
            {
                "platform": "Twitter / X",
                "type": "x_account",
                "keys": [
                    {"name": "api_key", "set": has_creds},
                    {"name": "api_secret", "set": has_creds},
                    {"name": "access_token", "set": has_creds},
                    {"name": "access_token_secret", "set": has_creds},
                ],
                "accounts": [a.account_label for a in accounts],
            }
        ]
        for platform, envs in CREDENTIAL_GROUPS:
            groups.append(
                {
                    "platform": platform,
                    "type": "env",
                    "keys": [
                        {"name": e, "set": bool(os.environ.get(e))} for e in envs
                    ],
                }
            )
        return groups

    @app.post("/api/credentials")
    def set_credential(body: SecretIn, account: str | None = None):
        """Store an encrypted credential.

        With ``account`` set the key is stored **per workspace**
        (``ws:<id>:secret:<ENV>``) and is *not* injected into the process env —
        used for AI keys. Without ``account`` it's a global secret (injected into
        ``os.environ``) — used for the shared source API keys.
        """
        if body.key not in ALLOWED_SECRET_ENVS:
            raise HTTPException(422, f"unknown credential key {body.key!r}")
        if not body.value.strip():
            raise HTTPException(422, "value must not be empty")
        from opensocial.core.secrets import encrypt_credentials

        with session_factory() as s:
            key = secret_key_or_400(s)
            if account:
                set_scoped_secret(s, account, body.key, body.value.strip(), key)
            else:
                blob = encrypt_credentials({"value": body.value.strip()}, key)
                set_app_setting(s, f"secret:{body.key}", blob.decode("ascii"))
                os.environ[body.key] = body.value.strip()
        return {"key": body.key, "set": True}

    @app.delete("/api/credentials")
    def delete_credential(key: str, account: str | None = None):
        """Remove a stored credential (per workspace with ``account``, else global).

        A global key is also dropped from the process env when it was the stored
        value that put it there; an operator-set env var is left alone.
        """
        from opensocial.core.db import AppSetting
        from opensocial.core.secrets import SecretsError, decrypt_credentials

        if key not in ALLOWED_SECRET_ENVS:
            raise HTTPException(422, f"unknown credential key {key!r}")
        setting = f"ws:{account}:secret:{key}" if account else f"secret:{key}"
        with session_factory() as s:
            row = s.get(AppSetting, setting)
            if row is not None:
                if not account:
                    try:
                        stored = decrypt_credentials(
                            row.value.encode("ascii"), resolve_settings(s).secret_key
                        ).get("value")
                    except SecretsError:
                        stored = None
                    if stored and os.environ.get(key) == stored:
                        os.environ.pop(key, None)
                s.delete(row)
                s.commit()
            still_set = (
                has_scoped_secret(s, account, key) if account else False
            ) or bool(os.environ.get(key))
        return {"key": key, "removed": row is not None, "set": still_set}

    @app.post("/api/credentials/x")
    def set_x_account(body: XAccountIn):
        from opensocial.core.secrets import encrypt_credentials

        creds = {
            "api_key": body.api_key,
            "api_secret": body.api_secret,
            "access_token": body.access_token,
            "access_token_secret": body.access_token_secret,
        }
        if not all(v.strip() for v in creds.values()):
            raise HTTPException(422, "all four X credentials are required")
        with session_factory() as s:
            key = secret_key_or_400(s)
            blob = encrypt_credentials(creds, key)
            acct = add_platform_account(
                s,
                account_label=body.label,
                credentials_encrypted=blob,
                daily_post_cap=body.daily_post_cap,
            )
            account_id = acct.id
        return {"id": account_id, "label": body.label, "set": True}

    # ---- accounts (multi-account management) --------------------------------

    @app.get("/api/accounts")
    def list_accounts():
        """Enrolled X accounts with their niche count and today's publish count."""
        now = datetime.now(timezone.utc)
        with session_factory() as s:
            accounts = list_platform_accounts(s, platform="x")
            out = []
            for a in accounts:
                # Niches are shared; a workspace's count is what it has selected.
                niche_count = len(get_followed_niches(s, a.id))
                out.append(
                    {
                        "id": a.id,
                        "label": a.account_label,
                        "platform": a.platform,
                        "daily_post_cap": a.daily_post_cap,
                        "niche_count": niche_count,
                        # True once X credentials are enrolled (non-empty blob).
                        "configured": bool(a.credentials_encrypted),
                        "published_today": published_today_count(
                            s, platform_account_id=a.id, day=now
                        ),
                    }
                )
        return out

    @app.post("/api/accounts")
    def create_account(body: WorkspaceIn):
        """Create a workspace by name (an X account with no credentials yet).

        This is the name-first flow: the workspace exists immediately so the
        user can configure its niches/AI/settings, and X credentials are
        enrolled later (Settings → Credentials) by re-using this label. Until
        credentials are enrolled the workspace can't publish live — only
        simulate in dry-run, which is the safe default.
        """
        label = body.label.strip()
        if not label:
            raise HTTPException(422, "workspace name must not be empty")
        if body.daily_post_cap is not None and body.daily_post_cap < 0:
            raise HTTPException(422, "daily_post_cap must be >= 0")
        with session_factory() as s:
            if get_platform_account_by_label(s, label) is not None:
                raise HTTPException(409, f"a workspace named {label!r} already exists")
            acct = add_platform_account(
                s,
                account_label=label,
                credentials_encrypted=b"",  # enrolled later
                daily_post_cap=body.daily_post_cap,
            )
            return {
                "id": acct.id,
                "label": acct.account_label,
                "daily_post_cap": acct.daily_post_cap,
            }

    @app.patch("/api/accounts/{account_id}")
    def patch_account(account_id: str, body: AccountPatch):
        if body.label is not None and not body.label.strip():
            raise HTTPException(422, "label must not be empty")
        if body.daily_post_cap is not None and body.daily_post_cap < 0:
            raise HTTPException(422, "daily_post_cap must be >= 0")
        with session_factory() as s:
            acct = update_platform_account(
                s,
                account_id,
                account_label=body.label,
                daily_post_cap=body.daily_post_cap,
                clear_cap=body.clear_cap,
            )
            if acct is None:
                raise HTTPException(404, "account not found")
            return {
                "id": acct.id,
                "label": acct.account_label,
                "daily_post_cap": acct.daily_post_cap,
            }

    @app.post("/api/accounts/{account_id}/credentials")
    def set_account_credentials(account_id: str, body: XAccountIn):
        """Enroll/replace *this workspace's* X credentials (per workspace).

        The workspace is one X account, so this writes the four encrypted X
        secrets onto that account — not a global, all-accounts card."""
        from opensocial.core.secrets import encrypt_credentials

        creds = {
            "api_key": body.api_key,
            "api_secret": body.api_secret,
            "access_token": body.access_token,
            "access_token_secret": body.access_token_secret,
        }
        if not all(v.strip() for v in creds.values()):
            raise HTTPException(422, "all four X credentials are required")
        with session_factory() as s:
            if get_platform_account(s, account_id) is None:
                raise HTTPException(404, "account not found")
            key = secret_key_or_400(s)
            blob = encrypt_credentials(creds, key)
            update_platform_account(s, account_id, credentials_encrypted=blob)
        return {"id": account_id, "configured": True}

    @app.post("/api/accounts/{account_id}/reset")
    def reset_account(account_id: str):
        """Reset just this workspace: clear its drafts, history, and settings.

        The workspace itself, its niche config files, the shared content pool,
        and sources are kept — unlike ``POST /api/reset`` which wipes the DB."""
        with session_factory() as s:
            if get_platform_account(s, account_id) is None:
                raise HTTPException(404, "account not found")
            deleted = reset_workspace(s, account_id, config_dir=config_dir)
        return {"ok": True, "id": account_id, "deleted": deleted}

    @app.delete("/api/accounts/{account_id}")
    def remove_account(account_id: str):
        """Delete a workspace (= account) and everything scoped to it: its
        niche config files, drafts + history, and per-workspace settings. The
        shared content pool and global sources are left intact."""
        with session_factory() as s:
            if get_platform_account(s, account_id) is None:
                raise HTTPException(404, "account not found")
            deleted = delete_workspace(s, account_id, config_dir=config_dir)
        return {"ok": True, "id": account_id, "deleted": deleted}

    return app
