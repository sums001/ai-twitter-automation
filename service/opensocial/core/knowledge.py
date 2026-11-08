"""The shared knowledge base: fetched items grouped into scored stories.

Everything here is free (no LLM). Three jobs:

1. **Ingest**: every item that passed some niche's filters is grouped into a
   :class:`~opensocial.core.db.Story`. Five outlets running the same event
   become one story with ``outlets=5`` instead of one candidate plus four
   discarded "duplicates", so coverage becomes a ranking signal.
2. **Score / shortlist**: rank a workspace's unused stories by coverage,
   freshness, momentum, relevance, source trust and how postable the headline
   is, discounted by similarity to what the workspace already posted, topic
   repetition, and how often the story was passed over.
3. **Memory**: per-workspace ``used`` / ``shown`` marks (:class:`StoryUsage`),
   so a story is never posted twice by one account and losers fade.

The single AI "pick" call (``ai/ranking.py``) only ever sees the top few
stories from :func:`shortlist`.
"""

from __future__ import annotations

import html as _html
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from opensocial.core.db import (
    ContentItemNiche,
    ContentItemRow,
    GeneratedPost,
    Story,
    StoryUsage,
)

# Story lifetime: an unused story whose newest item is older than this is
# deleted, and nothing older is matched or shortlisted. Adjustable at runtime
# via the ``story_ttl_hours`` app setting (see :func:`story_ttl_hours`).
DEFAULT_STORY_TTL_HOURS = 24
# Posted (used) stories are kept this long: the "don't repeat yourself" check
# and the synthesis take read them.
USED_STORY_KEEP_DAYS = 7
# Raw fetched items are kept this long. Longer than the story TTL on purpose:
# feeds re-serve articles for days, and deleting them sooner would make them
# come back as "new" on every fetch.
ITEM_KEEP_DAYS = 7
LOG_KEEP_DAYS = 30
COMMAND_KEEP_DAYS = 7
# How far back "what this account already posted" reaches for novelty.
POSTED_MEMORY_DAYS = 7
# A story this similar to something already posted is a repeat, not a candidate.
REPEAT_SIMILARITY = 0.6
# Freshness halves every this many hours since the story was first seen.
FRESHNESS_HALF_LIFE_HOURS = 12.0
# Items in this window count toward momentum ("still getting coverage").
MOMENTUM_HOURS = 6.0
# Each time a story makes the shortlist and loses, its score is multiplied by this.
SHOWN_DECAY = 0.85

_MAX_VARIANTS = 6  # title token-sets kept per story for matching
_MAX_ENTITIES = 30
_MAX_RECENT_TIMES = 10

# Score weights (sum to 1). Multipliers (novelty, variety, demotion) apply after.
WEIGHTS = {
    "coverage": 0.25,
    "freshness": 0.25,
    "relevance": 0.15,
    "postability": 0.15,
    "trust": 0.10,
    "momentum": 0.10,
}

# ---------------------------------------------------------------------------
# Text features
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset(
    "a an and are as at be been by for from has have had in into is it its of on "
    "or that the to was were will with this these those you your we our they "
    "their he she his her them after before over under amid via about than then "
    "so if but not no new says say said just now more most how why what when who "
    "which where while can could would should may might up down out off all any "
    "here there amp".split()
)
# Entities too common to tell two stories apart.
_COMMON_ENTITIES = frozenset(
    "ai us u.s uk eu ceo cfo ipo etf q1 q2 q3 q4 fy the new nyse nasdaq "
    "monday tuesday wednesday thursday friday saturday sunday january february "
    "march april may june july august september october november december".split()
)
# Ordinary words that often open a sentence-case headline (not names).
_PLAIN_OPENERS = frozenset(
    "stocks stock shares markets market investors traders analysts experts "
    "officials report reports study researchers scientists breaking exclusive "
    "watch opinion analysis update today tomorrow yesterday inside meet".split()
)
_WORD_RE = re.compile(r"[a-z0-9]+(?:[.'][a-z0-9]+)*")
# Mixed-case / all-caps names and tickers: OpenAI, GPT, MSFT, iOS-less, Q3.
_CAPSY_RE = re.compile(r"\b(?:[A-Z][a-z0-9]*[A-Z0-9][A-Za-z0-9]*|[A-Z]{2,})\b")
_CAP_WORD_RE = re.compile(r"\b[A-Z][a-zA-Z0-9]{2,}\b")
_NUMBER_RE = re.compile(
    r"\$?\d[\d,]*(?:\.\d+)?\s?(?:%|percent|[kmbt]n?\b|billion|million|trillion)?",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"^(?:19|20)\d\d$")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_text(raw: str | None) -> str:
    """HTML → plain text with collapsed whitespace."""
    if not raw:
        return ""
    return _WS_RE.sub(" ", _html.unescape(_TAG_RE.sub(" ", raw))).strip()


def _stem(word: str) -> str:
    """Tiny, consistent stemmer so 'pulls'/'pulling'/'pulled' match 'pull'."""
    if len(word) > 5 and word.endswith("ing"):
        return word[:-3]
    if len(word) > 5 and word.endswith("ed"):
        return word[:-2]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def title_tokens(title: str) -> set[str]:
    """Content-word tokens of a headline, stemmed, stopwords removed."""
    out = set()
    for w in _WORD_RE.findall((title or "").lower()):
        w = w.replace("'s", "")
        if w in _STOPWORDS or len(w) < 2:
            continue
        out.add(_stem(w))
    return out


def _norm_entity(text: str) -> str:
    return text.lower().replace("'s", "").replace(",", "").replace(" ", "").strip(".")


FIGURE_PREFIX = "#"  # marks a figure ($10k, 20%) apart from a name entity

_UNIT_RE = (
    (re.compile(r"(?:billion|bn)$"), "b"),
    (re.compile(r"(?:million|mn)$"), "m"),
    (re.compile(r"(?:trillion|tn)$"), "t"),
    (re.compile(r"percent$"), "%"),
)


def _norm_figure(text: str) -> str:
    """'$30 billion' and '$30B' both become '$30b'."""
    val = text.lower().replace(",", "").replace(" ", "").strip(".")
    for pat, unit in _UNIT_RE:
        val = pat.sub(unit, val)
    return val


def extract_entities(title: str, summary: str = "") -> set[str]:
    """Names, tickers and figures that identify *this* story.

    Headlines are often Title Case, so capitalization alone means little there.
    Signals used: mixed-case / all-caps words (OpenAI, MSFT), figures with units
    ($10K, 20%, stored with a ``#`` prefix), capitalized words in a
    sentence-case title, and proper nouns mid-sentence in the summary.
    """
    ents: set[str] = set()
    title = title or ""
    for m in _CAPSY_RE.findall(title):
        ents.add(_norm_entity(m))
    for m in _NUMBER_RE.findall(title):
        val = _norm_figure(m)
        if val and not _YEAR_RE.match(val) and (len(val) > 1 or not val.isdigit()):
            ents.add(FIGURE_PREFIX + val)
    words = title.split()
    long_words = [w for w in words if len(w) >= 4]
    capped = [w for w in long_words if w[:1].isupper()]
    sentence_case = bool(long_words) and len(capped) <= len(long_words) / 2
    if sentence_case:
        # In a sentence-case headline every capitalized word is a name, and so
        # is the opening word unless it is a plain word ("Why", "Stocks").
        for m in _CAP_WORD_RE.findall(" ".join(words[1:])):
            ents.add(_norm_entity(m))
        first = _norm_entity(re.sub(r"[^\w'.-]", "", words[0])) if words else ""
        if len(first) >= 3 and first not in _STOPWORDS and first not in _PLAIN_OPENERS:
            ents.add(first)
    if summary:
        # Capitalized words that don't start a sentence are proper nouns.
        for sentence in re.split(r"(?<=[.!?])\s+", summary[:400]):
            parts = sentence.split()
            for m in _CAP_WORD_RE.findall(" ".join(parts[1:])):
                ents.add(_norm_entity(m))
    return {e for e in ents if e and e not in _COMMON_ENTITIES and e not in _STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _split(ents: set[str]) -> tuple[set[str], set[str]]:
    names = {e for e in ents if not e.startswith(FIGURE_PREFIX)}
    return names, ents - names


def same_story(tokens_a: set[str], ents_a: set[str], tokens_b: set[str], ents_b: set[str]) -> float:
    """Similarity of two headlines in 0..1 (0 = different stories).

    Word overlap decides, with shared names lowering the bar: reworded
    headlines about the same event share few words but the same names. Two
    headlines about *different* named subjects are different stories even when
    an outlet's headline template makes their wording overlap ("A retiree who
    bought 1,000 Tesla shares..." vs "...1,000 Oracle shares...").
    """
    j = jaccard(tokens_a, tokens_b)
    names_a, figs_a = _split(ents_a)
    names_b, figs_b = _split(ents_b)
    shared_names = len(names_a & names_b)
    shared_figs = len(figs_a & figs_b)
    if names_a and names_b and not shared_names and j < 0.7:
        return 0.0
    if (
        j >= 0.5
        or (j >= 0.25 and shared_names >= 1)
        or (j >= 0.2 and shared_names >= 2)
        or (j >= 0.35 and shared_figs >= 1)
        or (shared_names >= 1 and shared_figs >= 2)
    ):
        return min(1.0, j + 0.1 * shared_names + 0.05 * shared_figs)
    return 0.0


def _clean_title(row: ContentItemRow) -> str:
    """Headline without HTML entities or Google News' trailing " - Outlet"."""
    title = clean_text(row.title)
    outlet = _google_outlet(row)
    if outlet and title.endswith(f" - {outlet}"):
        title = title[: -len(outlet) - 3].rstrip()
    return title


def _google_outlet(row: ContentItemRow) -> str | None:
    if row.source_name != "googlenews":
        return None
    src = (row.raw_metadata or {}).get("source")
    if isinstance(src, dict) and src.get("title"):
        return str(src["title"]).strip()
    return None


def _clean_summary(row: ContentItemRow, title: str) -> str:
    text = clean_text(row.summary or row.body)
    # Google News "summaries" just repeat the headline and outlet.
    if not text or title_tokens(text) <= title_tokens(title) | title_tokens(
        _google_outlet(row) or ""
    ):
        return ""
    return text[:800]


def outlet_of(row: ContentItemRow) -> str:
    """Who published the item: the outlet behind a feed, or the platform."""
    from opensocial.sources import get_source

    google = _google_outlet(row)
    if google:
        return google
    if row.source_name == "yfinance" and row.author:
        return row.author.strip()
    if row.source_name in ("reddit", "hackernews", "youtube", "medium", "devto", "arxiv"):
        try:
            return get_source(row.source_name).label or row.source_name
        except KeyError:
            return row.source_name
    domain = urlparse(row.url or "").netloc.lower()
    domain = domain[4:] if domain.startswith("www.") else domain
    return domain or row.source_name


def source_trust(source_name: str) -> float:
    from opensocial.sources import get_source

    try:
        return float(get_source(source_name).trust)
    except KeyError:
        return 0.5


def _as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


# ---------------------------------------------------------------------------
# Ingest: group items into stories
# ---------------------------------------------------------------------------


@dataclass
class IngestResult:
    items: int = 0
    new_stories: int = 0
    merged: int = 0


@dataclass
class _Active:
    story: Story
    variants: list[set[str]]
    entities: set[str]


def ingest(session: Session, *, now: datetime | None = None) -> IngestResult:
    """Group every not-yet-grouped item that passed a niche filter into stories.

    Items are matched against stories seen within the story TTL
    (oldest item first, so the original report anchors the story). A match adds
    the item's outlet, niche relevance and timestamp to the story; otherwise a
    new story starts. Idempotent: grouped items carry ``story_id`` and are
    skipped next time.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    passed = exists().where(
        ContentItemNiche.content_item_id == ContentItemRow.id,
        ContentItemNiche.status.in_(("candidate", "duplicate")),
    )
    rows = list(
        session.execute(
            select(ContentItemRow)
            .where(ContentItemRow.story_id.is_(None), passed)
            .order_by(ContentItemRow.published_at.asc())
        ).scalars()
    )
    result = IngestResult()
    if not rows:
        return result

    links: dict[str, dict[str, float]] = {}
    for link in session.execute(
        select(ContentItemNiche).where(
            ContentItemNiche.content_item_id.in_([r.id for r in rows]),
            ContentItemNiche.status.in_(("candidate", "duplicate")),
        )
    ).scalars():
        links.setdefault(link.content_item_id, {})[link.niche_slug] = float(
            link.relevance_score or 0.0
        )

    cutoff = now - timedelta(hours=story_ttl_hours(session))
    active: list[_Active] = []
    index: dict[str, set[int]] = {}

    def add_to_index(pos: int, keys) -> None:
        for key in keys:
            index.setdefault(key, set()).add(pos)

    for story in session.execute(
        select(Story).where(Story.last_seen_at >= cutoff.replace(tzinfo=None))
    ).scalars():
        variants = [set(v) for v in (story.tokens or [])]
        ents = set(story.entities or [])
        active.append(_Active(story, variants, ents))
        add_to_index(len(active) - 1, set().union(*variants) | ents if variants else ents)

    for row in rows:
        title = _clean_title(row)
        summary = _clean_summary(row, title)
        tokens = title_tokens(title)
        ents = extract_entities(title, summary)
        published = _as_utc(row.published_at) or now

        best, best_pos, best_sim = None, -1, 0.0
        candidates = set().union(*(index.get(k, set()) for k in tokens | ents)) if (tokens or ents) else set()
        outlet = outlet_of(row)
        for pos in candidates:
            act = active[pos]
            sim = max(
                (same_story(tokens, ents, v, act.entities) for v in act.variants),
                default=0.0,
            )
            # One outlet rarely runs the same event twice, but its templated
            # headlines often look alike: demand near-identical wording.
            if sim and outlet.lower() in {o.lower() for o in act.story.outlets or []}:
                sim = sim if max(jaccard(tokens, v) for v in act.variants) >= 0.6 else 0.0
            if sim > best_sim:
                best, best_pos, best_sim = act, pos, sim

        item_trust = source_trust(row.source_name)
        niche_rel = links.get(row.id, {})

        if best is None:
            story = Story(
                id=uuid.uuid4().hex,
                title=title,
                summary=summary or None,
                best_item_id=row.id,
                first_seen_at=published,
                last_seen_at=published,
                updated_at=now,
                item_count=1,
                outlets=[outlet],
                niche_relevance=dict(niche_rel),
                tokens=[sorted(tokens)],
                entities=sorted(ents)[:_MAX_ENTITIES],
                trust=item_trust,
                recent_item_times=[published.isoformat()],
            )
            session.add(story)
            session.flush()
            active.append(_Active(story, [tokens], set(ents)))
            add_to_index(len(active) - 1, tokens | ents)
            result.new_stories += 1
        else:
            story = best.story
            _merge_item(story, row, title, summary, tokens, ents, outlet,
                        item_trust, niche_rel, published, now)
            if len(best.variants) < _MAX_VARIANTS and tokens not in best.variants:
                best.variants.append(tokens)
            best.entities |= ents
            add_to_index(best_pos, tokens | ents)
            result.merged += 1

        row.story_id = story.id
        result.items += 1

    session.commit()
    return result


def _merge_item(story, row, title, summary, tokens, ents, outlet, item_trust,
                niche_rel, published, now) -> None:
    """Fold one more report of the event into an existing story."""
    outlets = list(story.outlets or [])
    if outlet.lower() not in {o.lower() for o in outlets}:
        outlets.append(outlet)
    story.outlets = outlets
    story.item_count = (story.item_count or 0) + 1

    rel = dict(story.niche_relevance or {})
    for slug, score in niche_rel.items():
        rel[slug] = max(score, rel.get(slug, 0.0))
    story.niche_relevance = rel

    variants = [list(v) for v in (story.tokens or [])]
    if len(variants) < _MAX_VARIANTS and sorted(tokens) not in variants:
        variants.append(sorted(tokens))
    story.tokens = variants
    story.entities = sorted(set(story.entities or []) | ents)[:_MAX_ENTITIES]

    times = list(story.recent_item_times or []) + [published.isoformat()]
    story.recent_item_times = sorted(times)[-_MAX_RECENT_TIMES:]
    first = _as_utc(story.first_seen_at)
    last = _as_utc(story.last_seen_at)
    if first is None or published < first:
        story.first_seen_at = published
    if last is None or published > last:
        story.last_seen_at = published

    # A more trusted outlet becomes the story's face: its headline, blurb, and
    # the URL fetched for full text. A real summary fills an empty one.
    if item_trust > (story.trust or 0.0):
        story.title = title
        story.best_item_id = row.id
        story.trust = item_trust
        if summary:
            story.summary = summary
    elif summary and not story.summary:
        story.summary = summary
    story.updated_at = now


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

# Headline patterns that make weak posts: SEO bait, listicles, promos, recaps.
_UNPOSTABLE = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^(is|are|should|could|can|will|does|do|did)\b.*\?$",
        r"^(ask|tell|show|launch)\s+hn\b",  # forum threads, not news
        r"\b(top|best)\s+\d+\b",
        r"\bhere'?s\s+(why|what|how|everything)\b",
        r"\bprice\s+prediction\b",
        r"\bstock\s+forecast\b",
        r"\bshould\s+you\s+(buy|sell)\b",
        r"\b(buy|sell)\s+(now|today)\b",
        r"\bfairly\s+valued\b",
        r"\bearnings\s+call\s+(highlights|transcript)\b",
        r"\b\d+%\s+off\b|\bdeals?\s+of\s+the\s+day\b|\bpromo\s+code\b",
        r"\bsponsored\b",
        r"\bhow\s+to\b",
        r"\bwhat\s+(you|we)\s+need\s+to\s+know\b",
        r"\bpodcast\b|\bepisode\b|\bnewsletter\b|\b(weekly|daily)\s+(recap|roundup)\b",
        r"^(watch|live|video)\s*[:|]",
        r"^\d+\s+(latest|top|best|biggest|things|ways|reasons|stocks?|picks)\b",
        r"^if\s+i\s+(could|had)\b|\bbuy\s+and\s+hold\b",
        r"\bmillionaire[- ]maker\b|\bretire\s+rich\b",
    )
]


def postability(title: str, entities: set[str] | list[str]) -> float:
    """How well a headline turns into a good post (0..1), by cheap heuristics."""
    score = 0.7
    for pat in _UNPOSTABLE:
        if pat.search(title or ""):
            score -= 0.25
    if re.search(r"\d", title or ""):
        score += 0.1
    if entities:
        score += 0.1
    return max(0.0, min(1.0, score))


def coverage_score(outlet_count: int) -> float:
    return 1.0 - 0.6 ** max(0, outlet_count - 1)


@dataclass
class ScoredStory:
    story: Story
    score: float
    niche_slug: str
    signals: dict[str, float] = field(default_factory=dict)

    @property
    def outlet_count(self) -> int:
        return len(self.story.outlets or [])


@dataclass
class _Posted:
    tokens: set[str]
    entities: set[str]
    niche: str


def _posted_memory(session: Session, workspace_id: str | None, now: datetime) -> list[_Posted]:
    """What this account recently posted or has queued, newest first."""
    since = (now - timedelta(days=POSTED_MEMORY_DAYS)).replace(tzinfo=None)
    stmt = (
        select(GeneratedPost, Story, ContentItemRow)
        .join(Story, Story.id == GeneratedPost.story_id, isouter=True)
        .join(ContentItemRow, ContentItemRow.id == GeneratedPost.content_item_id, isouter=True)
        .where(
            GeneratedPost.status.in_(("draft", "published")),
            GeneratedPost.created_at >= since,
        )
        .order_by(GeneratedPost.created_at.desc())
    )
    if workspace_id is not None:
        stmt = stmt.where(GeneratedPost.platform_account_id == workspace_id)
    out: list[_Posted] = []
    for post, story, item in session.execute(stmt).all():
        if story is not None:
            toks = set().union(*(set(v) for v in story.tokens)) if story.tokens else set()
            out.append(_Posted(toks, set(story.entities or []), post.niche_slug))
        elif item is not None:
            title = _clean_title(item)
            out.append(_Posted(title_tokens(title), extract_entities(title), post.niche_slug))
        else:  # synthesis take: remember what the text itself talked about
            out.append(_Posted(title_tokens(post.text), extract_entities(post.text), post.niche_slug))
    return out


def _momentum(story: Story, now: datetime) -> float:
    recent = 0
    for ts in story.recent_item_times or []:
        try:
            t = _as_utc(datetime.fromisoformat(ts))
        except ValueError:
            continue
        if t is not None and (now - t).total_seconds() <= MOMENTUM_HOURS * 3600:
            recent += 1
    return min(1.0, recent / 3.0)


def shortlist(
    session: Session,
    workspace_id: str | None,
    niche_slugs: list[str],
    *,
    now: datetime | None = None,
    k: int = 5,
    window_hours: float | None = None,
) -> list[ScoredStory]:
    """The ``k`` best unused stories for a workspace, best first. No LLM.

    Only stories tagged with one of ``niche_slugs`` and seen within
    ``window_hours`` (default: the story TTL) qualify. Stories this account already posted are excluded,
    and so are near-repeats of anything it posted or queued recently.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    wanted = set(niche_slugs)
    if not wanted:
        return []
    if window_hours is None:
        window_hours = story_ttl_hours(session)
    since = (now - timedelta(hours=window_hours)).replace(tzinfo=None)
    stories = list(
        session.execute(select(Story).where(Story.last_seen_at >= since)).scalars()
    )
    usage: dict[str, StoryUsage] = {}
    if workspace_id is not None and stories:
        usage = {
            u.story_id: u
            for u in session.execute(
                select(StoryUsage).where(StoryUsage.platform_account_id == workspace_id)
            ).scalars()
        }
    posted = _posted_memory(session, workspace_id, now)
    last3 = posted[:3]

    scored: list[ScoredStory] = []
    for story in stories:
        rel = {s: v for s, v in (story.niche_relevance or {}).items() if s in wanted}
        if not rel:
            continue
        use = usage.get(story.id)
        if use is not None and use.status == "used":
            continue

        tokens = set().union(*(set(v) for v in story.tokens)) if story.tokens else set()
        ents = set(story.entities or [])
        max_sim = max(
            (max(jaccard(tokens, p.tokens), same_story(tokens, ents, p.tokens, p.entities))
             for p in posted),
            default=0.0,
        )
        if max_sim >= REPEAT_SIMILARITY:
            continue  # the account already covered this event

        niche_slug = max(rel, key=rel.get)
        first = _as_utc(story.first_seen_at) or now
        age_h = max(0.0, (now - first).total_seconds() / 3600.0)
        signals = {
            "coverage": coverage_score(len(story.outlets or [])),
            "freshness": 0.5 ** (age_h / FRESHNESS_HALF_LIFE_HOURS),
            "relevance": rel[niche_slug],
            "postability": postability(story.title, ents),
            "trust": float(story.trust or 0.5),
            "momentum": _momentum(story, now),
        }
        base = sum(WEIGHTS[name] * value for name, value in signals.items())

        novelty = 1.0 - max_sim
        # Topic variety: don't hammer the same company/person/ticker back to back.
        names = _split(ents)[0]
        variety = 0.6 if any(names & _split(p.entities)[0] for p in last3) else 1.0
        demotion = SHOWN_DECAY ** (use.times_shown if use is not None else 0)
        signals.update(novelty=novelty, variety=variety, demotion=demotion)
        score = base * novelty * variety * demotion
        scored.append(ScoredStory(story, round(score, 4), niche_slug, signals))

    scored.sort(key=lambda s: s.score, reverse=True)
    return scored[:k]


def recent_top_stories(
    session: Session,
    niche_slugs: list[str],
    *,
    now: datetime | None = None,
    n: int = 3,
    hours: float = 48,
) -> list[Story]:
    """The biggest recent stories in these niches, used or not.

    Feeds the synthesis take when every story is already used: the account
    reads the room across several stories instead of repeating one.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    wanted = set(niche_slugs)
    since = (now - timedelta(hours=hours)).replace(tzinfo=None)
    rows = [
        s for s in session.execute(select(Story).where(Story.last_seen_at >= since)).scalars()
        if wanted & set((s.niche_relevance or {}).keys())
    ]

    def weight(s: Story) -> float:
        first = _as_utc(s.first_seen_at) or now
        age_h = max(0.0, (now - first).total_seconds() / 3600.0)
        return coverage_score(len(s.outlets or [])) + 0.5 ** (age_h / 24.0)

    rows.sort(key=weight, reverse=True)
    return rows[:n]


def has_stories(session: Session, niche_slugs: list[str], *, now: datetime | None = None) -> bool:
    """Whether the knowledge base holds anything at all for these niches."""
    return bool(
        recent_top_stories(session, niche_slugs, now=now, n=1, hours=story_ttl_hours(session))
    )


def story_items(session: Session, story: Story) -> list[ContentItemRow]:
    """The story's items, most trusted outlet first (the best item leads)."""
    items = list(
        session.execute(
            select(ContentItemRow).where(ContentItemRow.story_id == story.id)
        ).scalars()
    )
    items.sort(
        key=lambda r: (r.id != story.best_item_id, -source_trust(r.source_name),
                       r.source_name == "googlenews")
    )
    return items


# ---------------------------------------------------------------------------
# Per-workspace memory
# ---------------------------------------------------------------------------


def _usage(session: Session, story_id: str, workspace_id: str) -> StoryUsage:
    row = session.get(StoryUsage, (story_id, workspace_id))
    if row is None:
        row = StoryUsage(story_id=story_id, platform_account_id=workspace_id,
                         status="shown", times_shown=0)
        session.add(row)
    return row


def mark_used(
    session: Session,
    workspace_id: str | None,
    story_id: str,
    *,
    generated_post_id: str | None = None,
    now: datetime | None = None,
) -> None:
    """Remember that this account wrote a post from the story (never again)."""
    if workspace_id is None:
        return
    row = _usage(session, story_id, workspace_id)
    row.status = "used"
    row.used_at = (now or datetime.now(timezone.utc))
    row.generated_post_id = generated_post_id
    session.flush()


def mark_shown(
    session: Session,
    workspace_id: str | None,
    story_ids: list[str],
    *,
    now: datetime | None = None,
) -> None:
    """Count a shortlist appearance that didn't win, so losers fade."""
    if workspace_id is None:
        return
    when = now or datetime.now(timezone.utc)
    for sid in story_ids:
        row = _usage(session, sid, workspace_id)
        if row.status == "used":
            continue
        row.times_shown = (row.times_shown or 0) + 1
        row.last_shown_at = when
    session.flush()


def recent_post_lines(
    session: Session, workspace_id: str | None, *, n: int = 5, chars: int = 90
) -> list[str]:
    """One-line gists of the account's latest posts (for the pick prompt)."""
    stmt = (
        select(GeneratedPost.text)
        .where(GeneratedPost.status.in_(("draft", "published")))
        .order_by(GeneratedPost.created_at.desc())
        .limit(n)
    )
    if workspace_id is not None:
        stmt = stmt.where(GeneratedPost.platform_account_id == workspace_id)
    lines = []
    for (text,) in session.execute(stmt).all():
        one = " ".join((text or "").split())
        lines.append(one if len(one) <= chars else one[: chars - 1].rstrip() + "…")
    return lines


def score_gap(shortlisted: list[ScoredStory]) -> float:
    """Relative lead of #1 over #2 (1.0 when there is no #2)."""
    if len(shortlisted) < 2 or shortlisted[0].score <= 0:
        return 1.0
    return (shortlisted[0].score - shortlisted[1].score) / shortlisted[0].score


# ---------------------------------------------------------------------------
# Lifetime: forget stale knowledge so the database stops growing
# ---------------------------------------------------------------------------

_TTL_KEY = "story_ttl_hours"


def story_ttl_hours(session: Session) -> int:
    """How long an unused story lives (app setting, default 24h, minimum 1h)."""
    from opensocial.core.db import get_app_setting

    raw = get_app_setting(session, _TTL_KEY)
    try:
        return max(1, int(raw)) if raw else DEFAULT_STORY_TTL_HOURS
    except ValueError:
        return DEFAULT_STORY_TTL_HOURS


def set_story_ttl_hours(session: Session, hours: int) -> int:
    from opensocial.core.db import set_app_setting

    hours = int(hours)
    if not 1 <= hours <= 24 * 30:
        raise ValueError("story_ttl_hours must be between 1 and 720")
    set_app_setting(session, _TTL_KEY, str(hours))
    return hours


@dataclass
class PruneResult:
    stories: int = 0
    items: int = 0
    logs: int = 0
    commands: int = 0

    @property
    def total(self) -> int:
        return self.stories + self.items + self.logs + self.commands


def prune(session: Session, *, now: datetime | None = None) -> PruneResult:
    """Delete knowledge nobody will use again. Never touches posts or history.

    * unused stories (never posted by any account) whose newest item is older
      than the story TTL, with their shortlist memory;
    * posted stories older than ``USED_STORY_KEEP_DAYS``;
    * raw items older than ``ITEM_KEEP_DAYS`` that no draft or post points at
      (Queue and History keep their source links);
    * logs older than ``LOG_KEEP_DAYS`` and finished commands older than
      ``COMMAND_KEEP_DAYS``.

    SQLite reuses the freed pages, so the file stops growing without a VACUUM.
    """
    from sqlalchemy import delete

    from opensocial.core.db import Command, Log

    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)

    def ago(**kw):
        return (now - timedelta(**kw)).replace(tzinfo=None)

    used = select(StoryUsage.story_id).where(StoryUsage.status == "used")
    stale_unused = select(Story.id).where(
        Story.last_seen_at < ago(hours=story_ttl_hours(session)), Story.id.not_in(used)
    )
    old_used = select(Story.id).where(
        Story.last_seen_at < ago(days=USED_STORY_KEEP_DAYS), Story.id.in_(used)
    )
    doomed = [r[0] for r in session.execute(stale_unused.union(old_used)).all()]
    result = PruneResult()
    for i in range(0, len(doomed), 500):
        chunk = doomed[i:i + 500]
        session.execute(delete(StoryUsage).where(StoryUsage.story_id.in_(chunk)))
        result.stories += session.execute(delete(Story).where(Story.id.in_(chunk))).rowcount or 0

    referenced = select(GeneratedPost.content_item_id).where(
        GeneratedPost.content_item_id.is_not(None)
    )
    old_items = [
        r[0] for r in session.execute(
            select(ContentItemRow.id).where(
                ContentItemRow.fetched_at < ago(days=ITEM_KEEP_DAYS),
                ContentItemRow.id.not_in(referenced),
            )
        ).all()
    ]
    for i in range(0, len(old_items), 500):
        chunk = old_items[i:i + 500]
        session.execute(delete(ContentItemNiche).where(ContentItemNiche.content_item_id.in_(chunk)))
        result.items += session.execute(
            delete(ContentItemRow).where(ContentItemRow.id.in_(chunk))
        ).rowcount or 0

    result.logs = session.execute(
        delete(Log).where(Log.logged_at < ago(days=LOG_KEEP_DAYS))
    ).rowcount or 0
    result.commands = session.execute(
        delete(Command).where(
            Command.status.in_(("done", "failed")),
            Command.created_at < ago(days=COMMAND_KEEP_DAYS),
        )
    ).rowcount or 0
    session.commit()
    return result


__all__ = [
    "IngestResult",
    "ScoredStory",
    "clean_text",
    "extract_entities",
    "has_stories",
    "ingest",
    "jaccard",
    "mark_shown",
    "mark_used",
    "outlet_of",
    "prune",
    "set_story_ttl_hours",
    "story_ttl_hours",
    "postability",
    "recent_post_lines",
    "recent_top_stories",
    "same_story",
    "score_gap",
    "shortlist",
    "story_items",
    "title_tokens",
]

