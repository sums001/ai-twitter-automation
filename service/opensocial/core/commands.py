"""Dashboard→service command bridge.

The dashboard (via the HTTP API) drops a row in ``commands`` and the service
worker polls, executes it with its already-loaded modules, and records the
outcome. This module is that executor.

Supported command types:
* ``fetch_sources``    — payload ``{"niche": slug?, "source": name?}``
                         → fetch + store + re-filter content
* ``generate_posts``   — payload ``{"niche": slug?, "limit": n?}`` → write n
                         drafts (default 1) from the knowledge base
* ``run_slots``        — publish the workspace's next post now (a fresh draft,
                         or a new one), within the daily cap / minimum gap
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
from opensocial.core.autopilot import (
    ai_config_with_key,
    draft_for_workspace,
    post_next,
    record_fetch,
    workspace_context,
)
from opensocial.core.engine import (
    load_credentials_for_account,
    publish_post,
    resolve_account_for_niche,
)
from opensocial.core.filtering import filter_niche
from opensocial.core.knowledge import ingest
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
    skipped: list[str] | None = None,
) -> dict:
    """Fetch every enabled source for the followed niches; return new-item counts.

    Keyed ``"<niche>:<source>"``. When ``skipped`` is given, a plain-language
    reason is appended for everything that was *not* fetched (source turned
    off, missing key, no followed niche…), so a run that does nothing can say
    why instead of reporting a silent "no changes".
    """
    from opensocial.core.db import store_items
    from opensocial.sources import available_sources, get_source

    reasons: dict[str, None] = {}  # ordered set of skip reasons

    def skip(reason: str) -> None:
        reasons.setdefault(reason, None)

    def label(name: str) -> str:
        return getattr(get_source(name), "label", "") or name

    with session_factory() as s:
        statuses = {
            name: (row.enabled_globally, _stored_api_key(row, settings.secret_key))
            for name, row in source_statuses(s).items()
        }

    niche_list = _followed_niches(session_factory, config_dir, slug)
    known = set(available_sources())
    if not niche_list:
        skip(
            f"niche {slug!r} not found" if slug
            else "no niche is followed (pick niches on the Niches page)"
        )
    elif only_source and not any(only_source in n.sources for n in niche_list):
        if only_source in known:
            skip(f"{label(only_source)} isn't used by any followed niche")
        else:
            skip(f"unknown source {only_source!r}")

    async def run() -> dict:
        result: dict[str, int] = {}
        touched: set[str] = set()
        for niche in niche_list:
            if not niche.enabled:
                skip(f"niche {niche.slug} is disabled")
                continue
            for name, scfg in niche.sources.items():
                if only_source and name != only_source:
                    continue
                if name not in known:
                    continue  # stale entry for a removed source
                if scfg.get("enabled") is False:
                    skip(f"{label(name)} is off in the {niche.slug} niche")
                    continue
                # No status row → the source is disabled (fresh-DB default).
                enabled_globally, stored_key = statuses.get(name, (False, None))
                if not enabled_globally:
                    skip(f"{label(name)} is turned off on the Sources page")
                    continue
                if stored_key and not scfg.get("api_key"):
                    scfg = {**scfg, "api_key": stored_key}
                if get_source(name).missing_required_key(scfg):
                    skip(f"{label(name)} needs an API key (add it on the Sources page)")
                    continue
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
        # Fold what passed into the shared knowledge base (stories).
        if touched:
            with session_factory() as s:
                kb = ingest(s)
                if kb.items:
                    log(
                        s, "info",
                        f"knowledge base: {kb.items} item(s) → {kb.new_stories} new "
                        f"stor{'y' if kb.new_stories == 1 else 'ies'}, {kb.merged} merged",
                    )
                    s.commit()
        return result

    result = asyncio.run(run())
    if skipped is not None:
        skipped.extend(reasons)
    return result


_ai_config_with_key = ai_config_with_key  # back-compat alias


def _do_generate(
    session_factory, config_dir: str, workspace_id: str | None, slug: str | None, limit: int
) -> dict:
    """Write ``limit`` drafts for one workspace from the knowledge base."""
    from opensocial.core.db import ensure_default_workspace

    written = 0
    synthesis = 0
    with session_factory() as s:
        if workspace_id is None:
            workspace_id = ensure_default_workspace(s).id
        ctx = workspace_context(s, workspace_id, config_dir, slug=slug)
        if ctx is None:
            return {"error": "workspace not found"}
        if slug and not ctx.niches:
            return {"drafts": 0}
        if not ctx.niches:
            return {"error": "no niche is followed (pick niches on the Niches page)"}
        for _ in range(max(1, min(limit, 10))):
            draft = draft_for_workspace(s, ctx)
            if draft is None:
                break
            written += 1
            synthesis += draft.picked_by == "synthesis"
    if not written:
        return {"error": "nothing to write about yet: fetch sources first"}
    out = {"drafts": written}
    if synthesis:
        out["synthesis_takes"] = synthesis
    return out


def _do_run_slots(
    session_factory,
    config_dir: str,
    workspace_id: str | None,
    slug: str | None,
    settings: Settings,
) -> dict:
    """Publish the workspace's next post now, then restart its cadence."""
    from datetime import datetime, timezone

    from opensocial.core.db import ensure_default_workspace
    from opensocial.core.scheduler import next_post_time, set_next_post_at
    from opensocial.core.settings import resolve_schedule_tz

    now = datetime.now(timezone.utc)
    with session_factory() as s:
        if workspace_id is None:
            workspace_id = ensure_default_workspace(s).id
        ctx = workspace_context(s, workspace_id, config_dir, slug=slug)
        if ctx is None:
            return {"error": "workspace not found"}
        if not ctx.niches:
            return {"error": "no niche is followed (pick niches on the Niches page)"}
        outcome = post_next(s, ctx, now=now)
        if outcome.action == "posted":
            set_next_post_at(
                s, workspace_id, next_post_time(ctx.cadence, now, resolve_schedule_tz(s))
            )
            s.commit()
    if outcome.action == "posted":
        return {"published": 1, "dry_run": outcome.dry_run}
    if outcome.action == "failed":
        return {"error": outcome.detail}
    return {"published": 0, "reason": outcome.detail}


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
        from opensocial.core.settings import get_voice_samples

        niches = _niches(config_dir, post.niche_slug)
        config = {
            **(niches[0].raw if niches else {}),
            "ai": ai_config_with_key(s, post.platform_account_id),
        }
        samples, _ = get_voice_samples(s, post.platform_account_id)
        regenerate(s, post, config, voice_samples=samples)
    return {"ok": True}


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
                skipped: list[str] = []
                result = _do_fetch(
                    session_factory, config_dir, payload.get("niche"),
                    settings, payload.get("source"), skipped=skipped,
                )
                if result and not payload.get("niche") and not payload.get("source"):
                    record_fetch(session_factory)  # autopilot won't refetch right away
                if not result and skipped:
                    # Nothing ran at all: fail with the reason so the dashboard
                    # toast says why instead of vanishing as "done".
                    result = {"error": "nothing fetched: " + "; ".join(skipped)}
                elif skipped:
                    result = {**result, "skipped": "; ".join(skipped)}
            elif ctype == "generate_posts":
                result = _do_generate(
                    session_factory, config_dir, workspace_id,
                    payload.get("niche"), int(payload.get("limit", 1)),
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
