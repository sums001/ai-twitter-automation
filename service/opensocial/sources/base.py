"""Source plugin framework: the ``Source`` ABC and a name-based registry.

Adding a new content source means writing a new ``Source`` subclass,
decorating it with ``@register`` and importing its module in
``sources/__init__.py``. Each plugin fetches and normalizes its content into
``ContentItem`` objects.

Each plugin also **describes itself**: its label, what it fetches, whether it
needs an API key (``auth``), and which niche-config settings it reads. The
dashboard API, the credentials list and the fetch runner all read these class
attributes, so a source is declared in exactly one place.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, ClassVar

from opensocial.core.models import ContentItem

# Values for ``Source.auth``.
AUTH_NONE = "none"  # works out of the box
AUTH_OPTIONAL = "optional"  # works without a key; a key lifts rate limits
AUTH_REQUIRED = "required"  # won't fetch without a key

# Default cap on user-added origins (feeds / subreddits / channels) per source.
DEFAULT_MAX_ORIGINS = 20


@dataclass(frozen=True)
class Setting:
    """One niche-config field a source reads, e.g. ``query`` or ``limit``."""

    name: str
    help: str
    default: Any = None

DEFAULT_TIMEOUT = 20.0
USER_AGENT = "OpenX/0.1 (+https://github.com/openx)"


def resolve_api_key(
    config: dict,
    *env_names: str,
    required: bool = True,
    source_name: str = "source",
) -> str | None:
    """Find an API key from config ``api_key`` or the given env vars.

    Raises ``RuntimeError`` if ``required`` and none is found, so the CLI's
    per-source error handler reports it clearly without sinking the run.
    """
    key = config.get("api_key")
    if not key:
        for env in env_names:
            key = os.environ.get(env)
            if key:
                break
    if not key and required:
        envs = " or ".join(env_names) if env_names else "an API key"
        raise RuntimeError(
            f"{source_name} requires an API key (set config 'api_key' or env {envs})"
        )
    return key


def parse_iso8601(value: str | None) -> datetime:
    """Parse an ISO-8601 timestamp, defaulting to now (UTC) on failure."""
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class Source(ABC):
    """Base class for all content sources.

    Subclasses set ``name`` (unique, used as ``source_name`` and the registry
    key) and ``category`` (the default ``source_category``), and implement
    :meth:`fetch`. The remaining class attributes describe the source to the
    dashboard and the fetch runner.

    *Origin* sources (``origin_key`` set) are fed by the user: each origin is a
    feed URL / subreddit / channel appended to the niche config list named by
    ``origin_key``. Every other source simply runs on its niche config.
    """

    name: ClassVar[str]
    category: ClassVar[str]

    label: ClassVar[str] = ""  # display name
    description: ClassVar[str] = ""  # what it fetches, and from where
    auth: ClassVar[str] = AUTH_NONE
    key_envs: ClassVar[tuple[str, ...]] = ()  # env vars that hold the key
    key_help: ClassVar[str] = ""  # where to get the key / what it unlocks
    settings: ClassVar[tuple[Setting, ...]] = ()  # niche-config fields it reads
    upcoming: ClassVar[bool] = False  # shown in the dashboard, can't be enabled

    origin_key: ClassVar[str | None] = None
    origin_label: ClassVar[str] = ""  # placeholder for the add-origin input
    origin_defaults: ClassVar[tuple[tuple[str, Any], ...]] = ()  # new-block seed
    max_origins: ClassVar[int] = DEFAULT_MAX_ORIGINS

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or {}

    @abstractmethod
    async def fetch(self) -> list[ContentItem]:
        """Fetch and normalize content into ``ContentItem`` objects."""
        raise NotImplementedError

    # ---- self-description --------------------------------------------------

    @classmethod
    def key_available(cls, config: dict | None = None) -> bool:
        """True if a key is present (niche ``api_key`` or any ``key_envs``).

        Always True for sources that take no key.
        """
        if not cls.key_envs:
            return True
        if config and config.get("api_key"):
            return True
        return any(os.environ.get(env) for env in cls.key_envs)

    @classmethod
    def missing_required_key(cls, config: dict | None = None) -> bool:
        return cls.auth == AUTH_REQUIRED and not cls.key_available(config)

    @classmethod
    def parse_origin(cls, value: str) -> str | None:
        """Normalize user input (a URL, ``r/name``…) into a stored origin."""
        s = value.strip()
        return s or None

    @classmethod
    def origin_display(cls, value: str) -> str:
        """Human label for a stored origin."""
        return value

    @classmethod
    def describe(cls) -> dict:
        """JSON-friendly metadata for the dashboard."""
        return {
            "id": cls.name,
            "name": cls.label or cls.name,
            "category": cls.category,
            "description": cls.description,
            "auth": cls.auth,
            "key_envs": list(cls.key_envs),
            "key_help": cls.key_help,
            "settings": [asdict(s) for s in cls.settings],
            "upcoming": cls.upcoming,
            "origin_key": cls.origin_key,
            "origin_label": cls.origin_label if cls.origin_key else None,
            "max_origins": cls.max_origins if cls.origin_key else None,
        }


_REGISTRY: dict[str, type[Source]] = {}


def register(cls: type[Source]) -> type[Source]:
    """Class decorator that adds a ``Source`` subclass to the registry."""
    if not getattr(cls, "name", None):
        raise ValueError(f"{cls.__name__} must define a non-empty 'name'")
    if cls.name in _REGISTRY:
        raise ValueError(f"Duplicate source name: {cls.name!r}")
    _REGISTRY[cls.name] = cls
    return cls


def get_source(name: str) -> type[Source]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"Unknown source {name!r}. Available: {sorted(_REGISTRY)}"
        ) from None


def available_sources() -> list[str]:
    return sorted(_REGISTRY)
