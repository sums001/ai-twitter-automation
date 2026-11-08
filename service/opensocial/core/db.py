"""SQLite persistence layer (SQLAlchemy 2.0).

Phase 1 owns the ``content_items`` and ``content_item_niches`` tables and
mirrors the ``niche_profiles`` / ``source_configs`` config tables from the
schema in project.md. Later phases add the remaining tables.

Writes are idempotent: re-fetching the same content does not create
duplicates, satisfying the Phase 1 "de-duplicated across repeat runs"
requirement.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import uuid
from datetime import timedelta, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    sessionmaker,
)
from sqlalchemy.types import JSON

from opensocial.core.models import ContentItem


class Base(DeclarativeBase):
    pass


class ContentItemRow(Base):
    __tablename__ = "content_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_name: Mapped[str] = mapped_column(String, nullable=False)
    source_category: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    media_urls: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    language: Mapped[str] = mapped_column(String, default="en")
    sentiment: Mapped[float | None] = mapped_column(Float)
    engagement: Mapped[dict | None] = mapped_column(JSON)
    raw_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # The knowledge-base story this item was grouped into (NULL until ingested,
    # or when it never passed any niche's filters).
    story_id: Mapped[str | None] = mapped_column(String)


class Story(Base):
    """One real-world story in the shared knowledge base.

    Several outlets reporting the same event are grouped into one story, so
    coverage (how many distinct outlets ran it) becomes a ranking signal instead
    of being thrown away as "duplicates". Stories are shared across workspaces;
    whether a workspace already posted one lives in :class:`StoryUsage`.
    """

    __tablename__ = "stories"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    # The item that represents the story (highest-trust outlet): its URL is the
    # one fetched for full text, its media the passthrough image.
    best_item_id: Mapped[str | None] = mapped_column(String)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    outlets: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # niche slug -> best relevance score among the story's items in that niche
    niche_relevance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    tokens: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    entities: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    trust: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    # ISO timestamps of the story's most recent items, for the momentum signal.
    recent_item_times: Mapped[list] = mapped_column(JSON, default=list, nullable=False)


class StoryUsage(Base):
    """Per-workspace memory of a story: posted (``used``) or passed over.

    ``times_shown`` counts how often the story made a workspace's shortlist
    without being picked, so stories that keep losing fade instead of clogging
    the top of every shortlist.
    """

    __tablename__ = "story_usage"

    story_id: Mapped[str] = mapped_column(ForeignKey("stories.id"), primary_key=True)
    platform_account_id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="shown")  # shown|used
    times_shown: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_shown_at: Mapped[datetime | None] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    generated_post_id: Mapped[str | None] = mapped_column(String)


class ContentItemNiche(Base):
    __tablename__ = "content_item_niches"

    content_item_id: Mapped[str] = mapped_column(
        ForeignKey("content_items.id"), primary_key=True
    )
    niche_slug: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False)  # candidate|filtered|duplicate
    relevance_score: Mapped[float | None] = mapped_column(Float)


class NicheProfile(Base):
    __tablename__ = "niche_profiles"

    slug: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    config_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SourceConfig(Base):
    __tablename__ = "source_configs"

    source_name: Mapped[str] = mapped_column(String, primary_key=True)
    enabled_globally: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_fetch_status: Mapped[str | None] = mapped_column(String)
    extra_config: Mapped[dict | None] = mapped_column(JSON)


class GeneratedPost(Base):
    """A draft post produced by Phase 3 generation.

    ``content_item_id`` is NULL for **independent** posts (e.g. the daily Take)
    that have no source content behind them. ``priority_score`` is carried over
    from the candidate queue so the Phase 4 publisher can pick the best-scoring
    eligible draft at slot time. ``post_attempts`` / ``post_error`` back the
    publish retry state machine.
    """

    __tablename__ = "generated_posts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    content_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("content_items.id")
    )
    # The knowledge-base story the draft was written from (NULL for synthesis
    # takes and legacy drafts). Regenerating re-reads this story's facts.
    story_id: Mapped[str | None] = mapped_column(String)
    niche_slug: Mapped[str] = mapped_column(String, nullable=False)
    # The workspace (X account) that generated this draft. Niches are a shared
    # catalog, so this is what isolates one workspace's drafts from another's for
    # the same niche, and selects which drafts a workspace publishes. NULL means
    # "resolve the account at publish time" (legacy single-account installs).
    platform_account_id: Mapped[str | None] = mapped_column(String)
    post_type: Mapped[str] = mapped_column(String, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    media_path: Mapped[str | None] = mapped_column(Text)
    media_url: Mapped[str | None] = mapped_column(Text)
    media_attribution: Mapped[str | None] = mapped_column(Text)
    ai_text_provider: Mapped[str] = mapped_column(String, nullable=False)
    ai_image_provider: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")
    priority_score: Mapped[float | None] = mapped_column(Float)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime)
    post_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    post_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class PlatformAccount(Base):
    """A posting target (an X account today). Credentials are Fernet-encrypted."""

    __tablename__ = "platform_accounts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    platform: Mapped[str] = mapped_column(String, nullable=False, default="x")
    account_label: Mapped[str] = mapped_column(String, nullable=False)
    credentials_encrypted: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    daily_post_cap: Mapped[int | None] = mapped_column(Integer)


class PostHistory(Base):
    """One publish attempt's outcome, with its cost estimate."""

    __tablename__ = "post_history"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    generated_post_id: Mapped[str] = mapped_column(
        ForeignKey("generated_posts.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String, nullable=False, default="x")
    platform_account_id: Mapped[str | None] = mapped_column(String)
    platform_post_id: Mapped[str | None] = mapped_column(String)
    platform_post_url: Mapped[str | None] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, nullable=False)  # success | failed
    error_message: Mapped[str | None] = mapped_column(Text)
    attempted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    included_source_link: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cost_estimate: Mapped[float] = mapped_column(Float, nullable=False)


class Command(Base):
    """Dashboard→service bridge: the UI drops a row, the service polls + runs it."""

    __tablename__ = "commands"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class Log(Base):
    """Console output mirrored to the DB so the dashboard can show service logs."""

    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level: Mapped[str] = mapped_column(String, nullable=False)  # info | warn | error
    message: Mapped[str] = mapped_column(Text, nullable=False)
    logged_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class AppSetting(Base):
    """Key/value runtime settings the dashboard can change without a restart.

    Known keys: ``dry_run`` ("true"/"false"), ``app_mode`` ("manual"/"auto"),
    and ``secret:<ENV_NAME>`` for Fernet-encrypted API keys saved from the
    dashboard (base64 text of the encrypted blob).
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


def _json_serializer(obj) -> str:
    # raw_metadata can carry feed/API values that aren't natively JSON-safe
    # (e.g. time.struct_time, datetime); ``default=str`` makes them storable.
    return json.dumps(obj, default=str)


def make_engine(db_path: str | Path):
    """Create an engine and ensure all tables exist."""
    engine = create_engine(
        f"sqlite:///{db_path}", future=True, json_serializer=_json_serializer
    )
    Base.metadata.create_all(engine)
    _run_migrations(engine)
    return engine


# Additive, idempotent column migrations. ``create_all`` creates missing tables
# but never alters existing ones, so columns added after a DB was first created
# need an explicit ``ALTER TABLE``. Each entry is (table, column, DDL type);
# applied only when the column is absent. Keep these backward-compatible (new
# columns must be nullable / have a default) — there is no down-migration.
_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("generated_posts", "platform_account_id", "VARCHAR"),
    ("generated_posts", "story_id", "VARCHAR"),
    ("content_items", "story_id", "VARCHAR"),
]


# Indexes for the columns the knowledge base and its cleanup filter on, so
# per-tick queries stay fast however large the tables get. Idempotent.
_INDEX_MIGRATIONS: list[tuple[str, str, str]] = [
    ("ix_stories_last_seen_at", "stories", "last_seen_at"),
    ("ix_content_items_story_id", "content_items", "story_id"),
    ("ix_content_items_fetched_at", "content_items", "fetched_at"),
    ("ix_story_usage_account", "story_usage", "platform_account_id"),
    ("ix_generated_posts_content_item_id", "generated_posts", "content_item_id"),
    ("ix_logs_logged_at", "logs", "logged_at"),
]


def _run_migrations(engine) -> None:
    with engine.begin() as conn:
        for table, column, ddl_type in _COLUMN_MIGRATIONS:
            cols = {
                row[1]
                for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            if column not in cols:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"
                )
        for name, table, column in _INDEX_MIGRATIONS:
            conn.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS {name} ON {table} ({column})"
            )


def make_session_factory(db_path: str | Path) -> sessionmaker[Session]:
    return sessionmaker(bind=make_engine(db_path), future=True)


def _row_values(item: ContentItem) -> dict:
    data = item.model_dump()
    data["id"] = item.id
    return data


def store_items(
    session: Session, items: list[ContentItem], niche_slug: str
) -> tuple[int, int]:
    """Persist items and link each to ``niche_slug``.

    Returns ``(new_items, total_items)``. Existing ids are left untouched, so
    repeat runs add only genuinely new content. The niche link defaults to
    status ``candidate``; Phase 2 filtering refines that.
    """
    new_count = 0
    for item in items:
        result = session.execute(
            sqlite_insert(ContentItemRow)
            .values(**_row_values(item))
            .on_conflict_do_nothing(index_elements=["id"])
        )
        if result.rowcount:
            new_count += 1

        session.execute(
            sqlite_insert(ContentItemNiche)
            .values(
                content_item_id=item.id,
                niche_slug=niche_slug,
                status="candidate",
            )
            .on_conflict_do_nothing(
                index_elements=["content_item_id", "niche_slug"]
            )
        )

    session.commit()
    return new_count, len(items)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def insert_generated_post(
    session: Session,
    *,
    niche_slug: str,
    post_type: str,
    text: str,
    ai_text_provider: str,
    content_item_id: str | None = None,
    media_path: str | None = None,
    media_url: str | None = None,
    media_attribution: str | None = None,
    ai_image_provider: str | None = None,
    priority_score: float | None = None,
    status: str = "draft",
    platform_account_id: str | None = None,
    story_id: str | None = None,
) -> GeneratedPost:
    """Persist one draft post and return the ORM row (already flushed)."""
    now = _utcnow()
    post = GeneratedPost(
        id=uuid.uuid4().hex,
        content_item_id=content_item_id,
        story_id=story_id,
        niche_slug=niche_slug,
        platform_account_id=platform_account_id,
        post_type=post_type,
        text=text,
        media_path=media_path,
        media_url=media_url,
        media_attribution=media_attribution,
        ai_text_provider=ai_text_provider,
        ai_image_provider=ai_image_provider,
        status=status,
        priority_score=priority_score,
        post_attempts=0,
        created_at=now,
        updated_at=now,
    )
    session.add(post)
    session.flush()
    return post


# ---------------------------------------------------------------------------
# Phase 4 helpers — publish history, logs, command queue
# ---------------------------------------------------------------------------


def _day_bounds(day: datetime | None) -> tuple[datetime, datetime]:
    ref = (day or _utcnow()).astimezone(timezone.utc)
    start = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def record_post_history(
    session: Session,
    *,
    generated_post_id: str,
    status: str,
    included_source_link: bool,
    cost_estimate: float,
    platform: str = "x",
    platform_account_id: str | None = None,
    platform_post_id: str | None = None,
    platform_post_url: str | None = None,
    error_message: str | None = None,
) -> PostHistory:
    row = PostHistory(
        id=uuid.uuid4().hex,
        generated_post_id=generated_post_id,
        platform=platform,
        platform_account_id=platform_account_id,
        platform_post_id=platform_post_id,
        platform_post_url=platform_post_url,
        status=status,
        error_message=error_message,
        attempted_at=_utcnow(),
        included_source_link=included_source_link,
        cost_estimate=cost_estimate,
    )
    session.add(row)
    session.flush()
    return row


def published_today_count(
    session: Session,
    *,
    niche_slug: str | None = None,
    platform_account_id: str | None = None,
    day: datetime | None = None,
) -> int:
    """Number of successful publishes today (optionally scoped).

    Successful publishes are ``post_history`` rows with ``status='success'``;
    the niche filter joins back to ``generated_posts``. ``platform_account_id``
    filters on the recorded posting account. Used for the per-account and
    per-niche daily caps and for catch-up math.
    """
    start, end = _day_bounds(day)
    stmt = (
        select(func.count())
        .select_from(PostHistory)
        .where(
            PostHistory.status == "success",
            PostHistory.attempted_at >= start,
            PostHistory.attempted_at < end,
        )
    )
    if platform_account_id is not None:
        stmt = stmt.where(
            PostHistory.platform_account_id == platform_account_id
        )
    if niche_slug is not None:
        stmt = stmt.join(
            GeneratedPost, GeneratedPost.id == PostHistory.generated_post_id
        ).where(GeneratedPost.niche_slug == niche_slug)
    return int(session.execute(stmt).scalar() or 0)


def last_published_for_account(
    session: Session, platform_account_id: str | None
) -> datetime | None:
    """When this account last published successfully (any niche), for min-gap."""
    stmt = select(func.max(PostHistory.attempted_at)).where(
        PostHistory.status == "success"
    )
    if platform_account_id is not None:
        stmt = stmt.where(PostHistory.platform_account_id == platform_account_id)
    value = session.execute(stmt).scalar()
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def pending_draft_count(session: Session, platform_account_id: str | None) -> int:
    """Drafts awaiting review/publish for one account."""
    stmt = select(func.count()).select_from(GeneratedPost).where(
        GeneratedPost.status == "draft"
    )
    if platform_account_id is not None:
        stmt = stmt.where(GeneratedPost.platform_account_id == platform_account_id)
    return int(session.execute(stmt).scalar() or 0)


def log(session: Session, level: str, message: str) -> None:
    """Mirror a log line to the ``logs`` table for the dashboard."""
    session.add(Log(level=level, message=message, logged_at=_utcnow()))
    session.flush()


def add_platform_account(
    session: Session,
    *,
    account_label: str,
    credentials_encrypted: bytes,
    platform: str = "x",
    daily_post_cap: int | None = None,
) -> PlatformAccount:
    """Store (or update by label) an encrypted platform account."""
    existing = session.execute(
        select(PlatformAccount).where(
            PlatformAccount.platform == platform,
            PlatformAccount.account_label == account_label,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.credentials_encrypted = credentials_encrypted
        existing.daily_post_cap = daily_post_cap
        session.commit()
        return existing
    acct = PlatformAccount(
        id=uuid.uuid4().hex,
        platform=platform,
        account_label=account_label,
        credentials_encrypted=credentials_encrypted,
        daily_post_cap=daily_post_cap,
    )
    session.add(acct)
    session.commit()
    return acct


def list_platform_accounts(
    session: Session, *, platform: str | None = None
) -> list[PlatformAccount]:
    stmt = select(PlatformAccount)
    if platform is not None:
        stmt = stmt.where(PlatformAccount.platform == platform)
    return list(session.execute(stmt).scalars())


def default_platform_account(
    session: Session, *, platform: str = "x"
) -> PlatformAccount | None:
    """The account the publisher uses when none is specified (first by label)."""
    return session.execute(
        select(PlatformAccount)
        .where(PlatformAccount.platform == platform)
        .order_by(PlatformAccount.account_label)
    ).scalars().first()


def get_platform_account(
    session: Session, account_id: str
) -> PlatformAccount | None:
    """Fetch a platform account by id (``None`` if it no longer exists)."""
    if not account_id:
        return None
    return session.get(PlatformAccount, account_id)


def get_platform_account_by_label(
    session: Session, label: str, *, platform: str = "x"
) -> PlatformAccount | None:
    """Fetch a platform account by its human label."""
    return session.execute(
        select(PlatformAccount).where(
            PlatformAccount.platform == platform,
            PlatformAccount.account_label == label,
        )
    ).scalar_one_or_none()


def update_platform_account(
    session: Session,
    account_id: str,
    *,
    account_label: str | None = None,
    daily_post_cap: int | None = None,
    clear_cap: bool = False,
    credentials_encrypted: bytes | None = None,
) -> PlatformAccount | None:
    """Update an account's label, daily cap, and/or X credentials.

    ``clear_cap=True`` sets ``daily_post_cap`` back to NULL (unlimited);
    otherwise a non-None ``daily_post_cap`` is applied. A non-None
    ``credentials_encrypted`` replaces the stored (Fernet) credential blob.
    Returns the row, or ``None`` if the account doesn't exist.
    """
    acct = session.get(PlatformAccount, account_id)
    if acct is None:
        return None
    if account_label is not None and account_label.strip():
        acct.account_label = account_label.strip()
    if clear_cap:
        acct.daily_post_cap = None
    elif daily_post_cap is not None:
        acct.daily_post_cap = daily_post_cap
    if credentials_encrypted is not None:
        acct.credentials_encrypted = credentials_encrypted
    session.commit()
    return acct


def delete_platform_account(session: Session, account_id: str) -> bool:
    """Delete an account by id. Returns True if a row was removed."""
    acct = session.get(PlatformAccount, account_id)
    if acct is None:
        return False
    session.delete(acct)
    session.commit()
    return True


def delete_workspace(
    session: Session, workspace_id: str, *, config_dir: str | Path | None = None
) -> dict[str, int]:
    """Delete a workspace (= an X account) and everything scoped to it.

    Removes the account, its drafts + their publish history, and its
    ``ws:<id>:*`` settings (which include its followed-niche list). Niches are a
    **shared catalog**, so their config files are left untouched — deleting a
    workspace never removes a niche other workspaces may also follow. The shared
    ``content_items`` pool and global source config are likewise intact.
    ``config_dir`` is accepted for signature compatibility but unused. Returns
    deleted counts.
    """
    deleted: dict[str, int] = {}

    post_ids = [
        r[0]
        for r in session.execute(
            select(GeneratedPost.id).where(
                GeneratedPost.platform_account_id == workspace_id
            )
        ).all()
    ]
    if post_ids:
        deleted["post_history"] = (
            session.query(PostHistory)
            .filter(PostHistory.generated_post_id.in_(post_ids))
            .delete(synchronize_session=False)
        )
        deleted["generated_posts"] = (
            session.query(GeneratedPost)
            .filter(GeneratedPost.platform_account_id == workspace_id)
            .delete(synchronize_session=False)
        )

    deleted["app_settings"] = (
        session.query(AppSetting)
        .filter(AppSetting.key.like(f"ws:{workspace_id}:%"))
        .delete(synchronize_session=False)
    )
    # The shared story pool stays; only this workspace's used/shown memory goes.
    deleted["story_usage"] = (
        session.query(StoryUsage)
        .filter(StoryUsage.platform_account_id == workspace_id)
        .delete(synchronize_session=False)
    )

    acct = session.get(PlatformAccount, workspace_id)
    if acct is not None:
        session.delete(acct)
        deleted["platform_accounts"] = 1

    session.commit()

    # Remove the workspace's niche config files (disk is the source of truth).
    if config_dir is not None:
        removed = 0
        for path in Path(config_dir).glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if (data.get("account_id") or None) == workspace_id:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    pass
        deleted["niche_files"] = removed

    return deleted


def reset_workspace(
    session: Session, workspace_id: str, *, config_dir: str | Path | None = None
) -> dict[str, int]:
    """Reset one workspace's runtime data *without* deleting the workspace.

    Clears the workspace's generated posts + their publish history and removes
    its per-workspace settings (``ws:<id>:*``) so dry-run / app_mode / AI /
    followed niches revert to defaults. The workspace account, its niche config
    files, the shared content pool, sources, and logs are all left intact —
    this is the per-workspace analogue of :func:`reset_database`.
    """
    deleted: dict[str, int] = {}

    post_ids = [
        r[0]
        for r in session.execute(
            select(GeneratedPost.id).where(
                GeneratedPost.platform_account_id == workspace_id
            )
        ).all()
    ]
    if post_ids:
        deleted["post_history"] = (
            session.query(PostHistory)
            .filter(PostHistory.generated_post_id.in_(post_ids))
            .delete(synchronize_session=False)
        )
        deleted["generated_posts"] = (
            session.query(GeneratedPost)
            .filter(GeneratedPost.platform_account_id == workspace_id)
            .delete(synchronize_session=False)
        )

    deleted["app_settings"] = (
        session.query(AppSetting)
        .filter(AppSetting.key.like(f"ws:{workspace_id}:%"))
        .delete(synchronize_session=False)
    )
    # The shared story pool stays; only this workspace's used/shown memory goes.
    deleted["story_usage"] = (
        session.query(StoryUsage)
        .filter(StoryUsage.platform_account_id == workspace_id)
        .delete(synchronize_session=False)
    )

    session.commit()
    return deleted


def ensure_default_workspace(
    session: Session, *, label: str = "Default", platform: str = "x"
) -> PlatformAccount:
    """Guarantee at least one workspace exists, so the app always has one.

    On a fresh or pre-workspace install this creates an empty-credential
    workspace named ``label``; X credentials are enrolled later. When any
    account already exists it returns the first one untouched.
    """
    existing = list_platform_accounts(session, platform=platform)
    if existing:
        return existing[0]
    return add_platform_account(
        session, account_label=label, credentials_encrypted=b"", platform=platform
    )


def get_app_setting(session: Session, key: str) -> str | None:
    row = session.get(AppSetting, key)
    return row.value if row is not None else None


def set_app_setting(session: Session, key: str, value: str) -> None:
    row = session.get(AppSetting, key)
    if row is None:
        session.add(AppSetting(key=key, value=value, updated_at=_utcnow()))
    else:
        row.value = value
        row.updated_at = _utcnow()
    session.commit()


def upsert_source_status(
    session: Session,
    source_name: str,
    *,
    status: str | None = None,
    enabled: bool | None = None,
    extra_config: dict | None = None,
) -> SourceConfig:
    """Record a source's global toggle, last fetch outcome, or stored config."""
    row = session.get(SourceConfig, source_name)
    if row is None:
        row = SourceConfig(source_name=source_name, enabled_globally=True)
        session.add(row)
    if status is not None:
        row.last_fetch_at = _utcnow()
        row.last_fetch_status = status
    if enabled is not None:
        row.enabled_globally = enabled
    if extra_config is not None:
        merged = dict(row.extra_config or {})
        merged.update(extra_config)
        row.extra_config = merged
    session.commit()
    return row


def source_statuses(session: Session) -> dict[str, SourceConfig]:
    rows = session.execute(select(SourceConfig)).scalars()
    return {r.source_name: r for r in rows}


def reset_database(
    session: Session,
    *,
    clear_credentials: bool = False,
    config_dir: str | Path | None = None,
) -> dict[str, int]:
    """Wipe runtime data (content, drafts, history, logs, fetch status).

    Niche config files are kept, but when ``clear_credentials`` removes every
    workspace and ``config_dir`` is given, each niche's ``account_id`` is
    released (back to the unassigned shared pool) so niches don't end up owned
    by a deleted workspace — which would hide them from every workspace's view.
    ``clear_credentials`` also removes encrypted secrets and platform accounts.
    Returns a per-table count of deleted rows.
    """
    # Order matters: delete children before parents to respect FKs.
    tables = [
        ContentItemNiche,
        StoryUsage,
        Story,
        PostHistory,
        GeneratedPost,
        ContentItemRow,
        Command,
        Log,
        SourceConfig,
    ]
    if clear_credentials:
        tables += [PlatformAccount]

    deleted: dict[str, int] = {}
    for model in tables:
        count = session.execute(select(func.count()).select_from(model)).scalar() or 0
        session.query(model).delete()
        deleted[model.__tablename__] = int(count)

    def _delete_settings_like(pattern: str) -> int:
        rows = list(
            session.execute(
                select(AppSetting).where(AppSetting.key.like(pattern))
            ).scalars()
        )
        for row in rows:
            session.delete(row)
        return len(rows)

    # Selected niches are runtime state (stored in app_settings) — clear them so
    # a reset returns to the fresh-DB baseline of nothing selected / enabled.
    # Covers both the legacy global key and every workspace-scoped one.
    n_followed = _delete_settings_like("followed_niches")
    n_followed += _delete_settings_like("ws:%:followed_niches")
    if n_followed:
        deleted["followed_niches"] = n_followed

    if clear_credentials:
        # Accounts are gone, so all workspace-scoped settings (dry_run, app_mode,
        # ai, and per-workspace ``ws:<id>:secret:<ENV>``) are orphaned — drop the
        # whole ``ws:`` namespace plus any legacy global ``secret:`` keys.
        # Otherwise stored API keys would survive a "clear credentials" reset.
        n_secrets = _delete_settings_like("secret:%")
        n_ws = _delete_settings_like("ws:%")
        deleted["app_settings_secrets"] = n_secrets
        deleted["app_settings_workspace"] = n_ws

    session.commit()

    # All workspaces are gone — release every niche back to the shared pool so
    # none stays owned by a now-deleted workspace (which would hide it from the
    # Niches page in every workspace).
    if clear_credentials and config_dir is not None:
        released = 0
        for path in Path(config_dir).glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            if data.get("account_id"):
                data.pop("account_id", None)
                try:
                    path.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    released += 1
                except OSError:
                    pass
        deleted["niches_released"] = released

    return deleted


def enqueue_command(
    session: Session, type: str, payload: dict | None = None
) -> Command:
    cmd = Command(
        type=type, payload=payload, status="pending", created_at=_utcnow()
    )
    session.add(cmd)
    session.commit()
    return cmd


def claim_pending_commands(session: Session) -> list[Command]:
    """Atomically move pending commands to ``running`` and return them."""
    cmds = list(
        session.execute(
            select(Command).where(Command.status == "pending").order_by(Command.id)
        ).scalars()
    )
    for cmd in cmds:
        cmd.status = "running"
    session.commit()
    return cmds


def finish_command(
    session: Session, cmd: Command, *, status: str, result: dict | None = None
) -> None:
    cmd.status = status
    cmd.result = result
    cmd.finished_at = _utcnow()
    session.commit()
