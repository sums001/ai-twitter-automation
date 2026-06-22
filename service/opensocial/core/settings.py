"""Process-level operational settings, read from the environment.

These are the safety knobs absorbed from the reference implementation. The
defaults are deliberately conservative: publishing is a **dry-run** unless
explicitly switched live, and the app starts in **manual** mode.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    dry_run: bool
    app_mode: str  # "manual" | "auto"
    global_daily_cap: int
    max_post_attempts: int
    secret_key: str | None
    # Autopilot fetch cadence (minutes). In auto mode the worker refreshes the
    # draft queue this often, but only inside the day's posting window. 0 (or
    # negative) disables the timed refresh entirely.
    autopilot_fetch_minutes: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        # Fail-safe: live only when POST_DRY_RUN is *explicitly* a falsey string.
        # Anything else (unset, typo, "maybe") stays dry so nothing posts by
        # accident.
        raw = os.environ.get("POST_DRY_RUN")
        dry_run = True if raw is None else not _truthy_false(raw)

        mode = (os.environ.get("APP_MODE") or "manual").strip().lower()
        if mode not in ("manual", "auto"):
            mode = "manual"

        return cls(
            dry_run=dry_run,
            app_mode=mode,
            global_daily_cap=int(os.environ.get("GLOBAL_DAILY_CAP", "25")),
            max_post_attempts=int(os.environ.get("MAX_POST_ATTEMPTS", "3")),
            secret_key=os.environ.get("OPENSOCIAL_SECRET_KEY"),
            autopilot_fetch_minutes=int(
                os.environ.get("AUTOPILOT_FETCH_MINUTES", "30")
            ),
        )


def _truthy_false(value: str) -> bool:
    """True when the value explicitly means 'off' (i.e. go live)."""
    return value.strip().lower() in ("0", "false", "no", "off")


# ---------------------------------------------------------------------------
# Per-workspace settings storage
# ---------------------------------------------------------------------------
#
# A workspace is one X account (its ``PlatformAccount.id`` is the workspace id).
# Workspace-scoped settings live in ``app_settings`` under ``ws:<id>:<key>``;
# reads fall back to the legacy un-namespaced ``<key>`` (then env) so a
# pre-workspace install keeps working and its values become the defaults a new
# workspace inherits until it customizes them. Source config + the Fernet key +
# the shared fetch cadence stay global (un-namespaced).


def _ws_prefix(workspace_id: str | None) -> str:
    return f"ws:{workspace_id}:" if workspace_id else ""


def get_scoped_setting(session, workspace_id: str | None, key: str) -> str | None:
    """Read ``ws:<id>:key`` for a workspace, falling back to the legacy global
    ``key``. With no workspace, reads the legacy global key directly."""
    from opensocial.core.db import get_app_setting

    if workspace_id:
        scoped = get_app_setting(session, f"ws:{workspace_id}:{key}")
        if scoped is not None:
            return scoped
    return get_app_setting(session, key)


def set_scoped_setting(session, workspace_id: str | None, key: str, value: str) -> None:
    """Persist a workspace-scoped setting (global key when no workspace)."""
    from opensocial.core.db import set_app_setting

    set_app_setting(session, f"{_ws_prefix(workspace_id)}{key}", value)


# ---------------------------------------------------------------------------
# Per-workspace encrypted secrets (AI keys live here; source keys stay global)
# ---------------------------------------------------------------------------
#
# AI API keys are per workspace, stored Fernet-encrypted under
# ``ws:<id>:secret:<ENV>``. Reads fall back to the legacy global ``secret:<ENV>``
# (and the process env) so a pre-workspace install keeps working. Unlike global
# secrets these are **not** injected into ``os.environ`` — generation passes the
# resolved key explicitly so two workspaces can use different keys at once.


def set_scoped_secret(
    session, workspace_id: str | None, env_name: str, value: str, secret_key: str
) -> None:
    """Encrypt and store a workspace's API key under ``ws:<id>:secret:<ENV>``."""
    from opensocial.core.secrets import encrypt_credentials

    blob = encrypt_credentials({"value": value}, secret_key)
    set_scoped_setting(session, workspace_id, f"secret:{env_name}", blob.decode("ascii"))


def has_scoped_secret(session, workspace_id: str | None, env_name: str) -> bool:
    """Whether a workspace has this key (its own, or a legacy global default)."""
    return bool(get_scoped_setting(session, workspace_id, f"secret:{env_name}"))


def get_scoped_secret(
    session, workspace_id: str | None, env_name: str
) -> str | None:
    """Decrypt a workspace's API key (own value, else legacy global)."""
    from opensocial.core.secrets import SecretsError, decrypt_credentials

    raw = get_scoped_setting(session, workspace_id, f"secret:{env_name}")
    if not raw:
        return None
    secret_key = resolve_settings(session).secret_key
    try:
        return decrypt_credentials(raw.encode("ascii"), secret_key).get("value")
    except SecretsError:
        return None


def resolve_settings(session, workspace_id: str | None = None) -> Settings:
    """Env settings overlaid with the dashboard's runtime overrides.

    ``dry_run`` and ``app_mode`` are **per workspace** (``ws:<id>:…`` with a
    legacy global fallback) so each workspace publishes on its own terms. The
    fetch cadence and global cap stay global, and ``secret_key`` is global. The
    same fail-safe applies: dry-run only switches off on an explicit "false".
    """
    from dataclasses import replace

    from opensocial.core.db import get_app_setting

    base = Settings.from_env()

    dry = get_scoped_setting(session, workspace_id, "dry_run")
    if dry is not None:
        base = replace(base, dry_run=not _truthy_false(dry))

    mode = (get_scoped_setting(session, workspace_id, "app_mode") or "").strip().lower()
    if mode in ("manual", "auto"):
        base = replace(base, app_mode=mode)

    cap = get_app_setting(session, "global_daily_cap")
    if cap is not None:
        try:
            base = replace(base, global_daily_cap=max(0, int(cap)))
        except (TypeError, ValueError):
            pass

    fetch_min = get_app_setting(session, "autopilot_fetch_minutes")
    if fetch_min is not None:
        try:
            base = replace(base, autopilot_fetch_minutes=max(0, int(fetch_min)))
        except (TypeError, ValueError):
            pass

    if base.secret_key is None:
        from opensocial.core.secrets import keyfile_secret

        key = keyfile_secret()
        if key:
            base = replace(base, secret_key=key)

    return base


# ---------------------------------------------------------------------------
# Global AI provider config (moved out of per-niche config)
# ---------------------------------------------------------------------------

# One AI configuration applies to every niche. Stored as a JSON blob in
# ``app_settings`` under the ``ai`` key.
#
# ``text.provider`` is a friendly provider name (``claude`` / ``chatgpt`` /
# ``local`` / ``template``) rather than a library name; ``ai/text.py`` maps it
# to a concrete model + endpoint. ``endpoint`` is the API base URL — optional
# for the hosted providers (custom gateways), **required** for ``local``.
# API keys are never stored here: they live encrypted in the credentials store
# (``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``) and are read from the env.
DEFAULT_AI: dict = {
    "text": {
        "provider": "local",
        "model": "ollama/gemma3:4b",
        "endpoint": "http://localhost:11434",
        "temperature": 0.7,
    },
}


def load_ai_config(session, workspace_id: str | None = None) -> dict:
    """Resolve a workspace's AI config (stored blob overlaid on the defaults).

    Reads ``ws:<id>:ai`` with a legacy global fallback. Returns the ``ai`` block
    shape that ``get_text_provider`` / ``get_image_provider`` expect, so callers
    can inject it as ``config['ai']``.
    """
    raw = get_scoped_setting(session, workspace_id, "ai")
    if not raw:
        return {k: dict(v) for k, v in DEFAULT_AI.items()}

    try:
        stored = json.loads(raw)
    except (TypeError, ValueError):
        stored = {}

    return _merge_ai(stored)


def _merge_ai(stored: dict) -> dict:
    return {"text": {**DEFAULT_AI["text"], **(stored.get("text") or {})}}


def save_ai_config(session, config: dict, workspace_id: str | None = None) -> dict:
    """Persist a workspace's AI config (text generation only)."""
    resolved = _merge_ai(config or {})
    set_scoped_setting(session, workspace_id, "ai", json.dumps(resolved))
    return resolved


# ---------------------------------------------------------------------------
# Schedule timezone (global; the niche schedule is a shared catalog)
# ---------------------------------------------------------------------------
#
# Posting windows ("09:00"–"21:00") are interpreted in this timezone, and the
# dashboard renders today's slots in it. Stored as an IANA name (e.g.
# "America/New_York") in ``app_settings`` under ``schedule_timezone``; an empty
# value means the service's own local timezone — the historical behaviour.


def resolve_tz(name: str | None):
    """An IANA name → ``tzinfo``; ``None`` when empty or unknown.

    A ``None`` return tells callers to fall back to the server's local zone, so
    a missing ``tzdata`` package or a typo degrades gracefully rather than
    breaking slot resolution.
    """
    name = (name or "").strip()
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:
        return None


def get_schedule_timezone(session) -> str:
    """The configured schedule timezone IANA name, or "" for server-local."""
    from opensocial.core.db import get_app_setting

    return (get_app_setting(session, "schedule_timezone") or "").strip()


def set_schedule_timezone(session, name: str | None) -> str:
    """Validate and persist the schedule timezone (empty clears it to local)."""
    from opensocial.core.db import set_app_setting

    name = (name or "").strip()
    if name and resolve_tz(name) is None:
        raise ValueError(f"unknown timezone {name!r}")
    set_app_setting(session, "schedule_timezone", name)
    return name


def resolve_schedule_tz(session):
    """The configured schedule ``tzinfo`` (``None`` = server-local)."""
    return resolve_tz(get_schedule_timezone(session))


# ---------------------------------------------------------------------------
# Selected niches (the ones the user follows; gates fetch + generation)
# ---------------------------------------------------------------------------

# The user selects the niches that represent their domain. Only selected niches
# are fetched and drafted for; everything else stays dormant. There is no hard
# cap — but fewer, focused niches yield sharper, more relevant posts. Stored as
# a JSON list of slugs in ``app_settings`` under ``followed_niches``.


def get_followed_niches(session, workspace_id: str | None = None) -> list[str]:
    """Return a workspace's selected niche slugs (``[]`` if none set).

    Reads ``ws:<id>:followed_niches`` with a legacy global fallback.
    """
    raw = get_scoped_setting(session, workspace_id, "followed_niches")
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(value, list):
        return []
    return [str(s) for s in value if isinstance(s, str) and s.strip()]


def set_followed_niches(
    session, slugs: list[str], workspace_id: str | None = None
) -> list[str]:
    """Persist a workspace's selected niches, de-duplicated (no cap).

    Order is preserved; duplicates are dropped keeping first occurrence.
    """
    seen: list[str] = []
    for slug in slugs or []:
        s = str(slug).strip()
        if s and s not in seen:
            seen.append(s)
    set_scoped_setting(session, workspace_id, "followed_niches", json.dumps(seen))
    return seen
