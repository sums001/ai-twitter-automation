"""Niche tagging: which niches an item belongs to, and how relevant it is.

Each niche applies three cheap filters to the content linked to it:

* **blocklist**  — drop items containing a blocked keyword (whole words)
* **relevance**  — keep items matching enough relevance keywords; a headline
  hit counts three times a body hit
* **age limit**  — drop items older than ``max_age_days``

Surviving items are checked for **near-duplicates** (same story reprinted by
several outlets) and each ``content_item_niches`` link is marked ``candidate``,
``filtered`` or ``duplicate``. Candidates *and* duplicates then flow into the
knowledge base (``core/knowledge.py``), which groups them into stories and
ranks them; duplicates count as extra coverage there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from opensocial.core.db import ContentItemNiche, ContentItemRow

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[a-z0-9]+")
# Common words that shouldn't count toward near-duplicate similarity.
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have in is it its of on or that the "
    "to was were will with this these those you your we our they their he she".split()
)


@dataclass
class FilterConfig:
    """Per-niche filtering settings, parsed from the niche's ``filters`` block."""

    blocklist: list[str] = field(default_factory=list)
    relevance_keywords: list[str] = field(default_factory=list)
    relevance_threshold: int = 1  # min keyword hits to be considered relevant
    max_age_days: float | None = None
    dup_threshold: float = 0.8  # Jaccard similarity (0..1) above which = duplicate
    dup_window_days: float = 3.0  # only compare against items this recent (per spec)

    @classmethod
    def from_niche(cls, raw: dict) -> "FilterConfig":
        f = (raw or {}).get("filters") or {}
        return cls(
            blocklist=[w.lower() for w in f.get("blocklist", []) if w],
            relevance_keywords=[w.lower() for w in f.get("relevance_keywords", []) if w],
            relevance_threshold=int(f.get("relevance_threshold", 1)),
            max_age_days=(
                float(f["max_age_days"]) if f.get("max_age_days") is not None else None
            ),
            dup_threshold=float(f.get("dup_threshold", 0.8)),
            dup_window_days=float(f.get("dup_window_days", 3.0)),
        )


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def _item_text(row: ContentItemRow) -> str:
    """All the text a filter should look at, lower-cased."""
    parts = [row.title, row.summary or "", row.body or "", " ".join(row.tags or [])]
    return " ".join(parts).lower()


def _tokens(text: str) -> set[str]:
    return {t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b)


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


_KEYWORD_RE_CACHE: dict[str, re.Pattern] = {}

# A keyword hit in the headline is worth this many hits in the body: the title
# is what the story is *about*, the body mentions plenty in passing.
TITLE_WEIGHT = 3
# Weighted hits at which relevance saturates to 1.0 (two title hits).
_RELEVANCE_SATURATION = 2 * TITLE_WEIGHT


def _keyword_re(keyword: str) -> re.Pattern:
    """Whole-word matcher for a keyword or phrase, allowing a plural ``s``.

    Substring matching made ``ai`` match "said" and ``ml`` match "html"; word
    boundaries fix that while still matching "AIs" / "chips" for "ai" / "chip".
    """
    pat = _KEYWORD_RE_CACHE.get(keyword)
    if pat is None:
        pat = re.compile(
            rf"(?<![a-z0-9]){re.escape(keyword)}(?:e?s)?(?![a-z0-9])"
        )
        _KEYWORD_RE_CACHE[keyword] = pat
    return pat


def keyword_in(text: str, keyword: str) -> bool:
    """Whether ``keyword`` appears in lower-cased ``text`` as a whole word."""
    return bool(_keyword_re(keyword).search(text))


def _relevance_hits(title: str, rest: str, keywords: list[str]) -> tuple[int, int]:
    """Return ``(distinct_hits, weighted_hits)`` for whole-word keyword matches.

    ``distinct_hits`` counts keywords found anywhere (what the threshold is
    checked against); ``weighted_hits`` counts a title hit ``TITLE_WEIGHT``
    times so headline matches dominate the relevance score.
    """
    distinct = weighted = 0
    for kw in keywords:
        if keyword_in(title, kw):
            distinct += 1
            weighted += TITLE_WEIGHT
        elif keyword_in(rest, kw):
            distinct += 1
            weighted += 1
    return distinct, weighted


def _evaluate(row: ContentItemRow, cfg: FilterConfig, now: datetime) -> tuple[str, float]:
    """Return ``(status, relevance_score)`` for blocklist/relevance/age only.

    ``status`` is ``"filtered"`` or ``"candidate"``; near-duplicate detection
    happens afterward against the surviving candidates.
    """
    text = _item_text(row)

    for blocked in cfg.blocklist:
        if keyword_in(text, blocked):
            return "filtered", 0.0

    if cfg.max_age_days is not None:
        published = _as_utc(row.published_at)
        if published is not None:
            age_days = (now - published).total_seconds() / 86400.0
            if age_days > cfg.max_age_days:
                return "filtered", 0.0

    if cfg.relevance_keywords:
        title = (row.title or "").lower()
        hits, weighted = _relevance_hits(title, text, cfg.relevance_keywords)
        if hits < cfg.relevance_threshold:
            return "filtered", 0.0
        relevance = min(1.0, weighted / _RELEVANCE_SATURATION)
    else:
        relevance = 1.0  # no relevance keywords configured → everything is relevant

    return "candidate", round(relevance, 4)


def filter_niche(session: Session, niche_slug: str, raw_config: dict) -> dict[str, int]:
    """Apply filters + near-duplicate detection for one niche.

    Updates every ``content_item_niches`` row for the niche, setting
    ``status`` (candidate/filtered/duplicate) and ``relevance_score``.
    Returns a count per status.
    """
    cfg = FilterConfig.from_niche(raw_config)
    now = datetime.now(timezone.utc)

    rows = session.execute(
        select(ContentItemNiche, ContentItemRow)
        .join(ContentItemRow, ContentItemNiche.content_item_id == ContentItemRow.id)
        .where(ContentItemNiche.niche_slug == niche_slug)
        .order_by(ContentItemRow.published_at.asc())  # earliest is the "original"
    ).all()

    counts = {"candidate": 0, "filtered": 0, "duplicate": 0}
    # Accepted candidates kept as (published_at, title_tokens), oldest-first, for
    # near-dup comparison. Bounded to a recent window so the check compares only
    # against "recently seen content" (project.md) instead of all history —
    # which also keeps it from degrading to O(n^2) as the table grows.
    window = timedelta(days=cfg.dup_window_days)
    recent: list[tuple[datetime, set[str]]] = []

    for link, row in rows:
        status, relevance = _evaluate(row, cfg, now)

        if status == "candidate":
            published = _as_utc(row.published_at) or now
            # Rows arrive oldest-first, so anything older than the window vs. the
            # current item will stay out of range for all later items too — drop it.
            cutoff = published - window
            while recent and recent[0][0] < cutoff:
                recent.pop(0)

            tokens = _tokens(row.title)
            is_dup = any(
                _jaccard(tokens, seen) >= cfg.dup_threshold for _, seen in recent
            )
            if is_dup:
                status = "duplicate"
            else:
                recent.append((published, tokens))

        link.status = status
        # Duplicates keep their relevance: the knowledge base folds them into
        # the same story as extra coverage rather than discarding them.
        link.relevance_score = relevance if status != "filtered" else 0.0
        counts[status] += 1

    session.commit()
    return counts
