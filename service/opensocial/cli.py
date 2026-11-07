"""OpenX CLI.

Pipeline: ``fetch`` -> ``filter`` (tags niches, then groups items into
knowledge-base stories) -> ``stories`` (the scored shortlist) -> ``generate`` /
``preview`` (write a post) -> ``tick`` / ``post-next`` (autopilot). ``serve``
runs the dashboard API plus the background worker.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

import typer

from opensocial.core.autopilot import tick_all
from opensocial.core.commands import process_commands
from opensocial.core.config import NicheConfig, load_all_niches, load_niche
from opensocial.core.db import (
    GeneratedPost,
    add_platform_account,
    delete_platform_account,
    delete_workspace,
    get_platform_account,
    get_platform_account_by_label,
    list_platform_accounts,
    make_session_factory,
    store_items,
)
from opensocial.core.engine import (
    load_credentials_for_account,
    publish_post,
    resolve_account_for_niche,
)
from opensocial.core.filtering import filter_niche
from opensocial.core.settings import Settings
from opensocial.sources import available_sources, get_source

app = typer.Typer(help="OpenX automation service", no_args_is_help=True)


def _workspace(session, label: str | None):
    """The workspace (account) named ``label``, else the default one."""
    from opensocial.core.db import ensure_default_workspace

    if label:
        acct = get_platform_account_by_label(session, label)
        if acct is None:
            typer.echo(f"no workspace labelled '{label}'", err=True)
            raise typer.Exit(1)
        return acct
    return ensure_default_workspace(session)


def _report_tick(outcomes, session_factory) -> None:
    with session_factory() as s:
        labels = {a.id: a.account_label for a in list_platform_accounts(s)}
    for o in outcomes:
        when = f" (next {o.next_post_at:%H:%M} UTC)" if o.next_post_at else ""
        detail = f": {o.detail}" if o.detail else ""
        typer.echo(f"[{labels.get(o.workspace_id, o.workspace_id)}] {o.action}{detail}{when}")


async def _fetch_niche(niche: NicheConfig, session_factory) -> None:
    if not niche.enabled:
        typer.echo(f"[{niche.slug}] disabled — skipping")
        return

    typer.echo(f"[{niche.slug}] {niche.display_name}")
    for source_name, source_cfg in niche.sources.items():
        if source_cfg.get("enabled") is False:
            continue
        try:
            source_cls = get_source(source_name)
        except KeyError as exc:
            typer.echo(f"  ! {exc}")
            continue

        source = source_cls(source_cfg)
        try:
            items = await source.fetch()
        except Exception as exc:  # one bad source shouldn't sink the run
            typer.echo(f"  ! {source_name}: fetch failed ({exc})")
            continue

        with session_factory() as session:
            new, total = store_items(session, items, niche.slug)
        typer.echo(f"  + {source_name}: {new} new / {total} fetched")


async def _run_fetch(niches: list[NicheConfig], db: str) -> None:
    session_factory = make_session_factory(db)
    for niche in niches:
        await _fetch_niche(niche, session_factory)


@app.command()
def fetch(
    niche: str = typer.Option(
        None, "--niche", "-n", help="Slug of a single niche to fetch (default: all)."
    ),
    config_dir: str = typer.Option(
        "config/niches", "--config-dir", help="Directory of niche YAML files."
    ),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
) -> None:
    """Fetch and store content for one or all niches."""
    niches = _resolve_niches(niche, config_dir)
    asyncio.run(_run_fetch(niches, db))


def _resolve_niches(niche: str | None, config_dir: str) -> list[NicheConfig]:
    """Load a single niche by slug, or every niche in ``config_dir``."""
    if niche:
        path = Path(config_dir) / f"{niche}.json"
        if not path.exists():
            typer.echo(f"No niche config at {path}", err=True)
            raise typer.Exit(1)
        niches = [load_niche(path)]
    else:
        niches = load_all_niches(config_dir)

    if not niches:
        typer.echo(f"No niche configs found in {config_dir}", err=True)
        raise typer.Exit(1)
    return niches


@app.command(name="filter")
def filter_(
    niche: str = typer.Option(
        None, "--niche", "-n", help="Slug of a single niche to filter (default: all)."
    ),
    config_dir: str = typer.Option(
        "config/niches", "--config-dir", help="Directory of niche JSON files."
    ),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
) -> None:
    """Mark stored content as candidate/filtered/duplicate for one or all niches."""
    niches = _resolve_niches(niche, config_dir)
    session_factory = make_session_factory(db)
    for n in niches:
        with session_factory() as session:
            counts = filter_niche(session, n.slug, n.raw)
        typer.echo(
            f"[{n.slug}] {counts['candidate']} candidates, "
            f"{counts['filtered']} filtered, {counts['duplicate']} duplicates"
        )
    from opensocial.core.knowledge import ingest

    with session_factory() as session:
        kb = ingest(session)
    typer.echo(
        f"knowledge base: {kb.items} item(s) grouped, {kb.new_stories} new "
        f"stories, {kb.merged} merged into existing ones"
    )


@app.command()
def stories(
    niche: str = typer.Option(
        None, "--niche", "-n",
        help="Limit to one niche (default: the workspace's followed niches).",
    ),
    account: str = typer.Option(None, "--account", help="Workspace label (default: first)."),
    limit: int = typer.Option(10, "--limit", "-l", help="How many stories to show."),
    config_dir: str = typer.Option("config/niches", "--config-dir"),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
) -> None:
    """Show the knowledge base's scored shortlist for a workspace (no AI)."""
    from opensocial.core.autopilot import workspace_niches
    from opensocial.core.knowledge import ingest, shortlist

    session_factory = make_session_factory(db)
    with session_factory() as session:
        ingest(session)
        acct = _workspace(session, account)
        slugs = [n.slug for n in workspace_niches(session, acct.id, config_dir, niche)]
        if not slugs:
            typer.echo("no niches: follow some on the Niches page, or pass --niche")
            return
        ranked = shortlist(session, acct.id, slugs, k=limit)
        if not ranked:
            typer.echo(
                f"[{acct.account_label}] no unused stories for {', '.join(slugs)}: "
                "run fetch + filter?"
            )
            return
        typer.echo(
            f"[{acct.account_label}] top {len(ranked)} unused stories ({', '.join(slugs)}):"
        )
        for i, sc in enumerate(ranked, 1):
            sig = sc.signals
            typer.echo(f"  {i:>2}. [{sc.score:.3f}] {sc.story.title}")
            typer.echo(
                f"      {sc.outlet_count} outlet(s) | {sc.niche_slug} | "
                f"fresh {sig['freshness']:.2f} | coverage {sig['coverage']:.2f} | "
                f"postable {sig['postability']:.2f} | novelty {sig['novelty']:.2f}"
            )


@app.command(hidden=True)
def queue(
    niche: str = typer.Option(None, "--niche", "-n"),
    limit: int = typer.Option(10, "--limit", "-l"),
    config_dir: str = typer.Option("config/niches", "--config-dir"),
    db: str = typer.Option("opensocial.db", "--db"),
) -> None:
    """Old name for ``stories``."""
    stories(niche=niche, account=None, limit=limit, config_dir=config_dir, db=db)


def _print_draft(label: str, d, *, dry_run: bool) -> None:
    tag = "would write" if dry_run else "wrote"
    how = {"score": "top ranked", "ai": "AI pick", "synthesis": "synthesis take"}[d.picked_by]
    extra = f", {len(d.outlets)} outlet(s)" if d.outlets else ""
    extra += ", full article" if d.used_article else ""
    img = " [img]" if d.media_url else ""
    typer.echo(f"[{label}] {tag} <{d.post_type}>{img} from: {d.title} ({how}{extra})")
    typer.echo(d.text)
    typer.echo("")


def _draft_cmd(niche, account, count, config_dir, db, *, persist: bool) -> None:
    from opensocial.core.autopilot import draft_for_workspace, workspace_context

    session_factory = make_session_factory(db)
    with session_factory() as session:
        acct = _workspace(session, account)
        ctx = workspace_context(session, acct.id, config_dir, slug=niche)
        if not ctx.niches:
            typer.echo("no niches: follow some on the Niches page, or pass --niche", err=True)
            raise typer.Exit(1)
        for _ in range(count):
            d = draft_for_workspace(session, ctx, persist=persist)
            if d is None:
                typer.echo(
                    f"[{acct.account_label}] knowledge base is empty: run fetch + filter first"
                )
                return
            _print_draft(acct.account_label, d, dry_run=not persist)


@app.command()
def generate(
    niche: str = typer.Option(None, "--niche", "-n", help="Only this niche."),
    account: str = typer.Option(None, "--account", help="Workspace label (default: first)."),
    count: int = typer.Option(1, "--count", "-c", help="How many drafts to write."),
    config_dir: str = typer.Option("config/niches", "--config-dir"),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
) -> None:
    """Write draft post(s) from the knowledge base (marks their stories used)."""
    _draft_cmd(niche, account, count, config_dir, db, persist=True)


@app.command()
def preview(
    niche: str = typer.Option(None, "--niche", "-n", help="Only this niche."),
    account: str = typer.Option(None, "--account", help="Workspace label (default: first)."),
    config_dir: str = typer.Option("config/niches", "--config-dir"),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
) -> None:
    """Dry-run: show the post that would be written next, storing nothing."""
    _draft_cmd(niche, account, 1, config_dir, db, persist=False)


def _mode_banner(settings: Settings) -> None:
    mode = "DRY-RUN" if settings.dry_run else "LIVE"
    typer.echo(f"(app_mode={settings.app_mode}, publishing={mode})")


@app.command()
def keygen() -> None:
    """Print a fresh Fernet key for OPENSOCIAL_SECRET_KEY."""
    from opensocial.core.secrets import generate_key

    typer.echo(generate_key())


@app.command(name="account-add")
def account_add(
    label: str = typer.Option(..., "--label", help="A name for this account."),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
    daily_cap: int = typer.Option(
        None, "--daily-cap", help="Optional per-account daily post cap."
    ),
) -> None:
    """Enroll an X account, encrypting its credentials at rest.

    Reads the four X API credentials from the environment: X_API_KEY,
    X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET. Requires
    OPENSOCIAL_SECRET_KEY (make one with `keygen`).
    """
    import os

    from opensocial.core.secrets import SecretsError, encrypt_credentials
    from opensocial.core.settings import Settings

    needed = {
        "api_key": "X_API_KEY",
        "api_secret": "X_API_SECRET",
        "access_token": "X_ACCESS_TOKEN",
        "access_token_secret": "X_ACCESS_TOKEN_SECRET",
    }
    creds = {k: os.environ.get(env) for k, env in needed.items()}
    missing = [env for k, env in needed.items() if not creds[k]]
    if missing:
        typer.echo(f"Missing env vars: {', '.join(missing)}", err=True)
        raise typer.Exit(1)

    secret_key = Settings.from_env().secret_key
    try:
        blob = encrypt_credentials(creds, secret_key)
    except SecretsError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1)

    session_factory = make_session_factory(db)
    with session_factory() as session:
        add_platform_account(
            session, account_label=label, credentials_encrypted=blob,
            daily_post_cap=daily_cap,
        )
    typer.echo(f"stored X account '{label}' (credentials encrypted)")


@app.command(name="account-list")
def account_list(
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
) -> None:
    """List enrolled platform accounts (credentials are never printed)."""
    session_factory = make_session_factory(db)
    with session_factory() as session:
        accounts = list_platform_accounts(session)
    if not accounts:
        typer.echo("no accounts enrolled — see 'account-add'")
        return
    for a in accounts:
        cap = f" cap={a.daily_post_cap}" if a.daily_post_cap else ""
        typer.echo(f"{a.platform}: {a.account_label}{cap}  ({a.id})")


@app.command(name="account-rm")
def account_rm(
    label: str = typer.Option(..., "--label", help="Label of the account to remove."),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
) -> None:
    """Remove an enrolled X account by label."""
    session_factory = make_session_factory(db)
    with session_factory() as session:
        acct = get_platform_account_by_label(session, label)
        if acct is None:
            typer.echo(f"no account labelled '{label}'", err=True)
            raise typer.Exit(1)
        delete_platform_account(session, acct.id)
    typer.echo(f"removed X account '{label}'")


@app.command(name="workspace-list")
def workspace_list(
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
    config_dir: str = typer.Option("config/niches", "--config-dir"),
) -> None:
    """List workspaces (each is an X account) and their followed-niche counts."""
    from opensocial.core.settings import get_followed_niches

    session_factory = make_session_factory(db)
    with session_factory() as session:
        accounts = list_platform_accounts(session)
        if not accounts:
            typer.echo("no workspaces — create one with 'account-add'")
            return
        for a in accounts:
            # Niches are shared; a workspace's count is the niches it follows.
            n = len(get_followed_niches(session, a.id))
            cap = f" cap={a.daily_post_cap}" if a.daily_post_cap else ""
            typer.echo(f"{a.account_label}{cap} — {n} niche(s)  ({a.id})")


@app.command(name="workspace-rm")
def workspace_rm(
    label: str = typer.Option(..., "--label", help="Workspace (account) label."),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
    config_dir: str = typer.Option("config/niches", "--config-dir"),
) -> None:
    """Delete a workspace and everything scoped to it (account, its niches,
    drafts, history, and per-workspace settings). The shared content pool and
    sources are left intact."""
    session_factory = make_session_factory(db)
    with session_factory() as session:
        acct = get_platform_account_by_label(session, label)
        if acct is None:
            typer.echo(f"no workspace labelled '{label}'", err=True)
            raise typer.Exit(1)
        deleted = delete_workspace(session, acct.id, config_dir=config_dir)
    typer.echo(f"deleted workspace '{label}': {deleted}")


@app.command(name="niche-account")
def niche_account(
    niche: str = typer.Option(..., "--niche", "-n", help="Niche slug to bind."),
    account: str = typer.Option(
        None, "--account", help="Account label to bind (omit with --clear)."
    ),
    clear: bool = typer.Option(
        False, "--clear", help="Unassign the niche's account instead."
    ),
    config_dir: str = typer.Option("config/niches", "--config-dir"),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
) -> None:
    """Bind a niche to an X account (writes ``account_id`` into its JSON config)."""
    import json

    path = Path(config_dir) / f"{niche}.json"
    if not path.exists():
        typer.echo(f"no niche config at {path}", err=True)
        raise typer.Exit(1)

    account_id: str | None = None
    if not clear:
        if not account:
            typer.echo("pass --account <label> or --clear", err=True)
            raise typer.Exit(1)
        session_factory = make_session_factory(db)
        with session_factory() as session:
            acct = get_platform_account_by_label(session, account)
            if acct is None:
                typer.echo(f"no account labelled '{account}'", err=True)
                raise typer.Exit(1)
            account_id = acct.id

    data = json.loads(path.read_text(encoding="utf-8"))
    if clear:
        data.pop("account_id", None)
    else:
        data["account_id"] = account_id
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if clear:
        typer.echo(f"[{niche}] account unassigned")
    else:
        typer.echo(f"[{niche}] bound to account '{account}'")


@app.command()
def prune(
    hours: int = typer.Option(
        None, "--hours", help="Also set how long unused stories live (saved; default 24)."
    ),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
) -> None:
    """Forget stale knowledge now (the worker also does this hourly)."""
    from opensocial.core.knowledge import prune as kb_prune
    from opensocial.core.knowledge import set_story_ttl_hours, story_ttl_hours

    session_factory = make_session_factory(db)
    with session_factory() as session:
        if hours is not None:
            set_story_ttl_hours(session, hours)
        ttl = story_ttl_hours(session)
        r = kb_prune(session)
    typer.echo(
        f"unused stories live {ttl}h: removed {r.stories} stories, {r.items} old items, "
        f"{r.logs} log lines, {r.commands} commands"
    )


@app.command()
def tick(
    config_dir: str = typer.Option("config/niches", "--config-dir"),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
) -> None:
    """Run one autopilot tick for every workspace (posts/drafts only if due)."""
    session_factory = make_session_factory(db)
    _report_tick(tick_all(session_factory, config_dir=config_dir), session_factory)


@app.command(hidden=True)
def publish(
    config_dir: str = typer.Option("config/niches", "--config-dir"),
    db: str = typer.Option("opensocial.db", "--db"),
) -> None:
    """Old name for ``tick``."""
    tick(config_dir=config_dir, db=db)


@app.command(name="post-next")
def post_next_cmd(
    account: str = typer.Option(None, "--account", help="Workspace label (default: first)."),
    config_dir: str = typer.Option("config/niches", "--config-dir"),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
) -> None:
    """Publish a workspace's next post now (a fresh draft, or a new one).

    Respects the daily cap, minimum gap and the dry-run fail-safe.
    """
    from opensocial.core.autopilot import post_next, workspace_context

    session_factory = make_session_factory(db)
    with session_factory() as session:
        acct = _workspace(session, account)
        ctx = workspace_context(session, acct.id, config_dir)
        _mode_banner(ctx.settings)
        outcome = post_next(session, ctx)
    _report_tick([outcome], session_factory)


@app.command(name="post-now")
def post_now(
    post_id: str = typer.Argument(..., help="generated_posts.id to publish now."),
    config_dir: str = typer.Option("config/niches", "--config-dir"),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
) -> None:
    """Publish one specific draft immediately (respects the dry-run fail-safe)."""
    settings = Settings.from_env()
    _mode_banner(settings)
    session_factory = make_session_factory(db)
    with session_factory() as session:
        post = session.get(GeneratedPost, post_id)
        if post is None:
            typer.echo(f"No generated post {post_id}", err=True)
            raise typer.Exit(1)
        niches = _resolve_niches(post.niche_slug, config_dir)
        config = niches[0].raw if niches else {}
        account = (
            get_platform_account(session, post.platform_account_id)
            if post.platform_account_id
            else None
        ) or resolve_account_for_niche(session, config)
        if account is None and not settings.dry_run:
            typer.echo(
                f"[{post.niche_slug}] no account assigned — cannot publish",
                err=True,
            )
            raise typer.Exit(1)
        account_id = account.id if account is not None else None
        creds = (
            load_credentials_for_account(session, settings, account_id)
            if account_id
            else None
        )
        outcome = publish_post(
            session, post, config, settings, credentials=creds,
            platform_account_id=account_id,
        )
    verb = "would post" if outcome.dry_run else ("posted" if outcome.ok else "FAILED")
    typer.echo(f"[{post.niche_slug}] {verb} {outcome.post_id}: {outcome.error or 'ok'}")


@app.command()
def commands(
    config_dir: str = typer.Option("config/niches", "--config-dir"),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
) -> None:
    """Process the dashboard command queue once (a worker tick)."""
    session_factory = make_session_factory(db)
    ran = process_commands(session_factory, config_dir=config_dir)
    typer.echo(f"processed {ran} command(s)")


@app.command()
def run(
    interval: int = typer.Option(
        60, "--interval", help="Seconds between scheduler ticks."
    ),
    config_dir: str = typer.Option("config/niches", "--config-dir"),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
) -> None:
    """Run the worker loop without the dashboard: each tick drains the command
    queue and runs the autopilot. An overlap guard skips a tick if the previous
    one is still running. Ctrl-C to stop."""
    import threading

    from apscheduler.schedulers.blocking import BlockingScheduler

    from opensocial.core.settings import resolve_settings

    from opensocial.core.db import ensure_default_workspace

    session_factory = make_session_factory(db)
    with session_factory() as session:
        ensure_default_workspace(session)
        from opensocial.core.knowledge import ingest

        ingest(session)  # group any content stored before the knowledge base
        _mode_banner(resolve_settings(session))
    guard = threading.Lock()

    def tick() -> None:
        # Overlap guard: if the previous tick is still running, skip this one.
        if not guard.acquire(blocking=False):
            return
        try:
            process_commands(session_factory, config_dir=config_dir)
            # Autopilot: each workspace posts on its own cadence.
            tick_all(session_factory, config_dir=config_dir)
        finally:
            guard.release()

    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(tick, "interval", seconds=interval, max_instances=1)
    typer.echo(f"scheduler started (tick every {interval}s) — Ctrl-C to stop")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        typer.echo("scheduler stopped")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8765, "--port"),
    interval: int = typer.Option(
        5, "--interval", help="Seconds between background worker ticks."
    ),
    config_dir: str = typer.Option("config/niches", "--config-dir"),
    db: str = typer.Option("opensocial.db", "--db", help="SQLite database path."),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Print each HTTP request and a per-tick worker summary to the console.",
    ),
) -> None:
    """Run the dashboard API plus the background worker in one process.

    The worker drains the dashboard command queue and runs the autopilot (each
    workspace posts or drafts every X minutes inside its window), same as
    `run`, so a separate scheduler process isn't needed while serving.

    By default this is quiet (worker activity goes to the logs table / dashboard
    Logs page). Pass --verbose to mirror that activity to the terminal.
    """
    import threading
    import time as time_mod

    import uvicorn

    from opensocial.api import create_app, inject_stored_secrets
    from opensocial.core.settings import resolve_settings

    api = create_app(db, config_dir)
    session_factory = api.state.session_factory
    injected = inject_stored_secrets(session_factory)
    if injected:
        typer.echo(f"loaded {injected} stored secret(s) into the environment")
    from opensocial.core.knowledge import ingest

    with session_factory() as session:
        ingest(session)  # group any content stored before the knowledge base

    stop = threading.Event()
    guard = threading.Lock()

    def tick() -> None:
        if not guard.acquire(blocking=False):
            return
        try:
            ran = process_commands(session_factory, config_dir=config_dir)
            # Autopilot: each workspace posts (auto) or drafts (manual) every X
            # minutes inside its window; one shared fetch runs just before.
            outcomes = tick_all(session_factory, config_dir=config_dir)
            acted = [o.action for o in outcomes if o.action not in ("waiting", "idle")]
            if verbose and (ran or acted):
                typer.echo(
                    f"[{datetime.now():%H:%M:%S}] tick: {ran} command(s) run"
                    + (f", autopilot {', '.join(acted)}" if acted else "")
                )
        except Exception as exc:  # the worker must outlive any bad tick
            with session_factory() as session:
                from opensocial.core.db import log as db_log

                db_log(session, "error", f"worker tick failed: {exc}")
                session.commit()
            if verbose:
                typer.echo(f"[{datetime.now():%H:%M:%S}] worker tick failed: {exc}")
        finally:
            guard.release()

    def worker() -> None:
        while not stop.is_set():
            tick()
            stop.wait(interval)

    with session_factory() as session:
        _mode_banner(resolve_settings(session))
    thread = threading.Thread(target=worker, daemon=True, name="opensocial-worker")
    thread.start()
    typer.echo(f"API on http://{host}:{port} — worker tick every {interval}s")
    try:
        uvicorn.run(
            api, host=host, port=port,
            log_level="info" if verbose else "warning",
        )
    finally:
        stop.set()


@app.command()
def sources() -> None:
    """List registered source plugins."""
    for name in available_sources():
        typer.echo(name)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
