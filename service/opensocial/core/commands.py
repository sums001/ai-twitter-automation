"""Dashboard→service command bridge.

The dashboard (via the HTTP API) drops a row in ``commands`` and the service
worker polls, executes it with its already-loaded modules, and records the
outcome. This module is that executor.

Supported command types:
* ``fetch_sources``    — payload ``{"niche": slug?, "source": name?}``
                         → fetch + store + re-filter content
* ``generate_posts``   — payload ``{"niche": slug?, "limit": n?}`` → make drafts
* ``run_slots``        — payload ``{"niche": slug?}`` → publish posts due now
* ``post_now``         — payload ``{"generated_post_id": id}`` → publish one draft
* ``regenerate_post``  — payload ``{"generated_post_id": id}`` → re-run AI for one draft
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from opensocial.core.config import load_all_niches, load_niche
from opensocial.core.db import (
    Command,
    GeneratedPost,
    claim_pending_commands,
    finish_command,
    log,
    source_statuses,
    upsert_source_status,
)
from opensocial.core.engine import (
    load_credentials_for_account,
    publish_post,
    resolve_account_for_niche,
    run_due_slots,
)
from opensocial.core.filtering import filter_niche
from opensocial.core.generate import generate_for_niche, generate_independent
from opensocial.core.settings import Settings


def _niches(config_dir: str, slug: str | None):
    if slug:
        path = Path(config_dir) / f"{slug}.json"
        return [load_niche(path)] if path.exists() else []
    return load_all_niches(config_dir)


def _followed_niches(session_factory, config_dir: str, slug: str | None):
    """The niches a **shared fetch** should cover: the union of every
    workspace's followed niches (each workspace owns its niches via
    ``account_id``). An explicit ``slug`` still targets that single niche.

    Fetching is one shared pass across all workspaces — content is deduped in
    ``content_items`` and linked per-niche in ``content_item_niches``. Before any
    workspace exists, falls back to the legacy global followed list over all
    niches so a pre-workspace install keeps fetching.
    """
    if slug:
        return _niches(config_dir, slug)

    from opensocial.core.db import list_platform_accounts
    from opensocial.core.settings import get_followed_niches

    niches = load_all_niches(config_dir)
    with session_factory() as s:
        accounts = list_platform_accounts(s)
        if not accounts:
            wanted = set(get_followed_niches(s, None))
        else:
            # Union of every workspace's followed niches. Fetch only needs to
            # know a niche is wanted by *someone*; ownership (``account_id``,
            # used by generate/publish) is deliberately not required here so a
            # followed/owned drift never silently stops fetching.
            wanted = set().union(
                *(set(get_followed_niches(s, a.id)) for a in accounts)
            )
    return [n for n in niches if n.slug in wanted]


def _workspace_niches(session_factory, config_dir: str, workspace_id: str, slug: str | None):
    """A single workspace's target niches for generate/publish — its followed
    niches from the shared catalog.

    With ``slug`` set, that one niche (so the dashboard can act on demand);
    otherwise the workspace's followed list. Niches are shared across
    workspaces, so selection is the per-workspace followed list (namespaced),
    not niche ownership — two workspaces may follow the same niche and each
    generates/publishes independently under its own account.
    """
    if slug:
        return _niches(config_dir, slug)
    from opensocial.core.settings import get_followed_niches

    with session_factory() as s:
        followed = set(get_followed_niches(s, workspace_id))
    return [n for n in load_all_niches(config_dir) if n.slug in followed]


def _stored_api_key(source_row, secret_key: str | None) -> str | None:
    """Decrypt a dashboard-saved API key from ``source_configs.extra_config``."""
    extra = (source_row.extra_config or {}) if source_row is not None else {}
    blob = extra.get("api_key_enc")
    if not blob:
        return None
    from opensocial.core.secrets import SecretsError, decrypt_credentials

    try:
        return decrypt_credentials(blob.encode("ascii"), secret_key).get("api_key")
    except SecretsError:
        return None


def _do_fetch(
    session_factory,
    config_dir: str,
    slug: str | None,
    settings: Settings,
    only_source: str | None = None,
) -> dict:
    from opensocial.core.db import store_items
    from opensocial.sources import get_source

    with session_factory() as s:
        statuses = {
            name: (row.enabled_globally, _stored_api_key(row, settings.secret_key))
            for name, row in source_statuses(s).items()
        }

    niche_list = _followed_niches(session_factory, config_dir, slug)

    async def run() -> dict:
        result: dict[str, int] = {}
        touched: set[str] = set()
        for niche in niche_list:
            if not niche.enabled:
                continue
            for name, scfg in niche.sources.items():
                if only_source and name != only_source:
                    continue
                if scfg.get("enabled") is False:
                    continue
                # No status row → the source is disabled (fresh-DB default).
                enabled_globally, stored_key = statuses.get(name, (False, None))
                if not enabled_globally:
                    continue
                if stored_key and not scfg.get("api_key"):
                    scfg = {**scfg, "api_key": stored_key}
                try:
                    items = await get_source(name)(scfg).fetch()
                except Exception as exc:  # one bad source shouldn't sink the run
                    with session_factory() as s:
                        upsert_source_status(s, name, status=f"error: {exc}")
                        log(s, "error", f"fetch {name} failed: {exc}")
                        s.commit()
                    continue
                with session_factory() as s:
                    new, total = store_items(s, items, niche.slug)
                    upsert_source_status(s, name, status="ok")
                result[f"{niche.slug}:{name}"] = new
                touched.add(niche.slug)

        # Re-filter every niche that got new content so candidates are fresh.
        for niche in niche_list:
            if niche.slug not in touched:
                continue
            with session_factory() as s:
                counts = filter_niche(s, niche.slug, niche.raw)
                log(
                    s, "info",
                    f"[{niche.slug}] filter: {counts['candidate']} candidates, "
                    f"{counts['filtered']} filtered, {counts['duplicate']} duplicates",
                )
                s.commit()
        return result

    return asyncio.run(run())


def _ai_config_with_key(session, workspace_id: str | None) -> dict:
    """A workspace's AI config with its own API key resolved into ``text``.

    AI keys are per workspace; this injects the decrypted key (the workspace's,
    else a legacy global default) so generation authenticates with the right
    one without relying on a single shared ``os.environ`` value.
    """
    from opensocial.ai.text import PROVIDER_KEY_ENV
    from opensocial.core.settings import get_scoped_secret, load_ai_config

    ai_cfg = load_ai_config(session, workspace_id)
    provider = ((ai_cfg.get("text") or {}).get("provider") or "").lower()
    env = PROVIDER_KEY_ENV.get(provider)
    if env:
        key = get_scoped_secret(session, workspace_id, env)
        if key:
            ai_cfg = {**ai_cfg, "text": {**ai_cfg["text"], "api_key": key}}
    return ai_cfg


def _do_generate(
    session_factory, config_dir: str, workspace_id: str, slug: str | None, limit: int
) -> dict:
    """Generate drafts for one workspace using its own AI config + niches."""
    from opensocial.core.settings import get_scoped_secret

    result: dict[str, int] = {}
    for niche in _workspace_niches(session_factory, config_dir, workspace_id, slug):
        if not niche.enabled:
            continue
        with session_factory() as s:
            config = {**niche.raw, "ai": _ai_config_with_key(s, workspace_id)}
            # Unsplash is a per-workspace key (like the AI keys): inject the
            # workspace's own so ``get_image_provider`` can authenticate without
            # a shared ``os.environ`` value.
            unsplash_key = get_scoped_secret(s, workspace_id, "UNSPLASH_ACCESS_KEY")
            if unsplash_key:
                config["unsplash_access_key"] = unsplash_key
            drafts = generate_for_niche(
                s, niche.slug, config, limit=limit,
                platform_account_id=workspace_id,
            )
            drafts += generate_independent(
                s, niche.slug, config, platform_account_id=workspace_id,
            )
        result[niche.slug] = len(drafts)
    return result


def _do_run_slots(
    session_factory,
    config_dir: str,
    workspace_id: str,
    slug: str | None,
    settings: Settings,
) -> dict:
    from dataclasses import replace

    # An explicit "run due now" from the dashboard dispatches even in manual
    # mode — the app_mode guard exists to stop *unattended* publishing only.
    settings = replace(settings, app_mode="auto")
    from opensocial.core.db import get_platform_account

    published = 0
    for niche in _workspace_niches(session_factory, config_dir, workspace_id, slug):
        if not niche.enabled:
            continue
        with session_factory() as s:
            # Niches are shared, so publish as *this* workspace's account (not
            # one resolved from the niche), drawing only its own drafts.
            account = get_platform_account(s, workspace_id) if workspace_id else None
            outcomes = run_due_slots(
                s, niche.slug, niche.raw, settings, account=account
            )
        published += sum(1 for o in outcomes if o.ok)
    return {"published": published}


def _do_post_now(session_factory, config_dir: str, post_id: str, settings: Settings) -> dict:
    from opensocial.core.settings import resolve_settings

    with session_factory() as s:
        post = s.get(GeneratedPost, post_id)
        if post is None:
            return {"error": "post not found"}
        niches = _niches(config_dir, post.niche_slug)
        config = niches[0].raw if niches else {}
        # Prefer the account stamped on the draft; otherwise resolve from the
        # niche (or the sole account). Holds if it can't be determined.
        account = None
        if post.platform_account_id:
            from opensocial.core.db import get_platform_account

            account = get_platform_account(s, post.platform_account_id)
        if account is None:
            account = resolve_account_for_niche(s, config)
        # Honour the owning workspace's dry-run setting for this manual publish.
        settings = resolve_settings(s, account.id if account is not None else None)
        # Hold only for live posting; dry-run still simulates without an account.
        if account is None and not settings.dry_run:
            return {"error": "no account assigned for this niche"}
        account_id = account.id if account is not None else None
        creds = (
            load_credentials_for_account(s, settings, account_id)
            if account_id
            else None
        )
        outcome = publish_post(
            s, post, config, settings, credentials=creds,
            platform_account_id=account_id,
        )
    return {"ok": outcome.ok, "dry_run": outcome.dry_run, "error": outcome.error}


def _do_regenerate(session_factory, config_dir: str, post_id: str) -> dict:
    from opensocial.core.approval import regenerate

    with session_factory() as s:
        post = s.get(GeneratedPost, post_id)
        if post is None:
            return {"error": "post not found"}
        niches = _niches(config_dir, post.niche_slug)
        config = {
            **(niches[0].raw if niches else {}),
            "ai": _ai_config_with_key(s, post.platform_account_id),
        }
        regenerate(s, post, config)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Autopilot: timed fetch→generate that keeps the draft queue topped up.
# ---------------------------------------------------------------------------


def _window_for_niches(niches, now, tz=None):
    """Posting window (first, last) across a list of scheduled niches today.

    ``(None, None)`` when none are scheduled. Both tz-aware; windows are
    interpreted in ``tz`` (``None`` = server-local).
    """
    from opensocial.core.scheduler import ScheduleConfig, resolve_slots

    first = last = None
    for niche in niches:
        if not niche.enabled:
            continue
        slots = resolve_slots(
            ScheduleConfig.from_niche(niche.raw), niche.slug, now, tz=tz
        )
        if not slots:
            continue
        if first is None or slots[0] < first:
            first = slots[0]
        if last is None or slots[-1] > last:
            last = slots[-1]
    return first, last


def _auto_workspaces(session_factory):
    """Workspace ids currently in ``auto`` app mode (per-workspace setting)."""
    from opensocial.core.db import list_platform_accounts
    from opensocial.core.settings import resolve_settings

    with session_factory() as s:
        ids = [a.id for a in list_platform_accounts(s)]
        return [wid for wid in ids if resolve_settings(s, wid).app_mode == "auto"]


def autopilot_refresh(
    session_factory: sessionmaker,
    *,
    config_dir: str = "config/niches",
    settings: Settings | None = None,
    now=None,
) -> dict | None:
    """Top up the draft queue with fresh data, on a timed cadence.

    The fetch is **shared** across workspaces; generation is **per workspace**.
    It does work only when *now* falls inside the posting window of the
    auto-mode workspaces — from ``X`` minutes before the first slot up to ``X``
    before the last — and at most once per ``X`` minutes. ``X`` is the global
    ``autopilot_fetch_minutes``; 0 disables it. ``settings`` is accepted for
    back-compat but per-workspace app mode is resolved internally.
    """
    from datetime import datetime, timedelta, timezone

    from opensocial.core.db import get_app_setting, set_app_setting
    from opensocial.core.settings import resolve_schedule_tz, resolve_settings

    with session_factory() as s:
        x_min = int(resolve_settings(s, None).autopilot_fetch_minutes)
        tz = resolve_schedule_tz(s)
    if x_min <= 0:
        return None

    auto_ids = _auto_workspaces(session_factory)
    if not auto_ids:
        return None

    now = now or datetime.now(timezone.utc)
    # Window spans the auto workspaces' followed niches.
    auto_niches = [
        n
        for wid in auto_ids
        for n in _workspace_niches(session_factory, config_dir, wid, None)
    ]
    first, last = _window_for_niches(auto_niches, now, tz=tz)
    if first is None:
        return None

    x = timedelta(minutes=x_min)
    window_start = first - x  # warm-up before the first post
    window_end = last - x  # last fetch precedes the last post by X
    now_local = now.astimezone(tz)
    if now_local < window_start or now_local > window_end:
        return None

    # Throttle: at most one refresh per X minutes across ticks.
    with session_factory() as s:
        last_raw = get_app_setting(s, "autopilot_last_refresh")
    if last_raw:
        try:
            last_ts = datetime.fromisoformat(last_raw)
            if now - last_ts < x:
                return None
        except ValueError:
            pass

    # One shared fetch pass for all workspaces, then per-workspace generation.
    with session_factory() as s:
        fetch_settings = resolve_settings(s, None)
    fetched = _do_fetch(session_factory, config_dir, None, fetch_settings)
    generated: dict[str, int] = {}
    for wid in auto_ids:
        generated.update(_do_generate(session_factory, config_dir, wid, None, limit=5))
    with session_factory() as s:
        set_app_setting(s, "autopilot_last_refresh", now.isoformat())
        new_items = sum(fetched.values())
        new_drafts = sum(generated.values())
        log(
            s, "info",
            f"autopilot refresh: {new_items} new item(s), {new_drafts} draft(s)",
        )
        s.commit()
    return {"fetched": fetched, "generated": generated}


def process_commands(
    session_factory: sessionmaker,
    *,
    config_dir: str = "config/niches",
    settings: Settings | None = None,
) -> int:
    """Claim and execute all pending commands. Returns how many were run."""
    if settings is None:
        from opensocial.core.settings import resolve_settings

        with session_factory() as s:
            settings = resolve_settings(s)
    with session_factory() as s:
        claimed = claim_pending_commands(s)
        # Detach the lightweight fields we need so the session can close.
        jobs = [(c.id, c.type, dict(c.payload or {})) for c in claimed]

    for cmd_id, ctype, payload in jobs:
        workspace_id = payload.get("workspace_id") or payload.get("workspace")
        try:
            if ctype == "fetch_sources":
                # Fetch is shared across workspaces; a niche/source narrows it.
                result = _do_fetch(
                    session_factory, config_dir, payload.get("niche"),
                    settings, payload.get("source"),
                )
            elif ctype == "generate_posts":
                result = _do_generate(
                    session_factory, config_dir, workspace_id,
                    payload.get("niche"), int(payload.get("limit", 5)),
                )
            elif ctype == "run_slots":
                from opensocial.core.settings import resolve_settings

                with session_factory() as s:
                    ws_settings = resolve_settings(s, workspace_id)
                result = _do_run_slots(
                    session_factory, config_dir, workspace_id,
                    payload.get("niche"), ws_settings,
                )
            elif ctype == "post_now":
                result = _do_post_now(
                    session_factory, config_dir,
                    payload.get("generated_post_id"), settings,
                )
            elif ctype == "regenerate_post":
                result = _do_regenerate(
                    session_factory, config_dir, payload.get("generated_post_id")
                )
            else:
                result = {"error": f"unknown command type {ctype!r}"}
            status = "failed" if result.get("error") else "done"
        except Exception as exc:  # never let one command kill the worker
            result, status = {"error": str(exc)}, "failed"

        with session_factory() as s:
            cmd = s.get(Command, cmd_id)
            if cmd is not None:
                finish_command(s, cmd, status=status, result=result)
            # Mirror the outcome into the logs table so the dashboard Logs tab
            # shows what the worker did — successes and, crucially, failures
            # (e.g. an LLM BadRequest) that previously only surfaced as a
            # transient toast on the command result.
            label = _describe_command(ctype, payload)
            if status == "failed":
                log(s, "error", f"{label} failed: {result.get('error')}")
            else:
                log(s, "info", f"{label} done: {_summarize_result(result)}")
            s.commit()
    return len(jobs)


def _describe_command(ctype: str, payload: dict) -> str:
    """Short human label for a command, including its niche/source target."""
    target = payload.get("niche") or payload.get("source")
    suffix = f" [{target}]" if target else ""
    return f"{ctype}{suffix}"


def _summarize_result(result: dict) -> str:
    """Compact one-line summary of a command result dict for the logs."""
    if not result:
        return "no changes"
    parts = [f"{k}={v}" for k, v in result.items()]
    return ", ".join(parts)
