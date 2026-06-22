"""Phase 3 — turn prioritized candidates into draft posts.

For each niche this:

1. takes the top of the Phase 2 candidate queue (skipping items already
   turned into a draft),
2. assigns a **post type** (respecting per-type daily caps),
3. generates standalone, link-free text via the niche's persona prompt,
4. post-processes it (strip wrapping quotes, URL-aware length check with one
   rewrite-to-fit pass),
5. attaches an image per the type's visual rule,
6. and writes a ``draft`` row to ``generated_posts``.

A separate :func:`generate_independent` job produces the niche's **independent
post(s)** — e.g. the daily Take — with no content item behind them.
"""

from __future__ import annotations

import html as _html
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from opensocial.ai.images import ImageProvider, get_image_provider
from opensocial.ai.prompts import build_messages, unsplash_query
from opensocial.ai.ranking import (
    choose_image_query,
    choose_post_type,
    rerank_by_importance,
)
from opensocial.ai.text import TextProvider, TextProviderError, get_text_provider
from opensocial.core.approval import initial_status
from opensocial.core.config import niche_account_id
from opensocial.core.db import (
    GeneratedPost,
    content_ids_with_posts,
    insert_generated_post,
)
from opensocial.core.filtering import candidate_queue
from opensocial.core.posttypes import (
    INDEPENDENT_ELIGIBLE,
    PostTypesConfig,
    shuffled_deck,
)

DEFAULT_CHAR_LIMIT = 280
URL_WEIGHT = 23  # t.co wraps every link to a flat 23 chars
_URL_RE = re.compile(r"https?://\S+")
# Wrapping quotes/backticks a model sometimes adds around the whole post.
_WRAP_RE = re.compile(r'^\s*["“”\'`]+|["“”\'`]+\s*$')

# Source bodies/summaries arrive as HTML (RSS/Atom content, Reddit feeds, Google
# News descriptions). Strip tags + entities so the prompt's CONTEXT is clean
# plain text — otherwise small models latch onto markup and stray numbers.
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Markdown + dash punctuation a model may add despite the prompt rules. These
# back the "plain text, no markdown, no dashes" rules deterministically.
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_MD_CODE_RE = re.compile(r"`+([^`]+)`+")
_MD_ITALIC_RE = re.compile(r"(?<!\w)[*_](\S.*?\S|\S)[*_](?!\w)")
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE)
# Every Unicode "dash punctuation" variant a model may emit as a sentence break,
# not just the em/en dash: figure dash, horizontal bar, two-/three-em dash, the
# minus sign and small/fullwidth forms all read as dashes and must become commas.
# ASCII '-' is intentionally excluded here so intra-word hyphens (GPT-4) survive;
# spaced ASCII dashes are handled by _SPACED_HYPHEN_RE below.
_DASH_RE = re.compile(
    "\\s*["
    "‒–—―"  # figure, en, em dash, horizontal bar
    "−"                    # minus sign
    "⸺⸻"              # two-em, three-em dash
    "﹘﹣－"        # small em dash, small/fullwidth hyphen-minus
    "]\\s*"
)
_SPACED_HYPHEN_RE = re.compile(r"\s+-\s+")  # " - " used as a dash → comma
_COMMA_FIXUP_RE = re.compile(r"\s+,")
_DOUBLE_COMMA_RE = re.compile(r",\s*,")
_MULTISPACE_RE = re.compile(r"[ \t]{2,}")

# Leading "scaffolding" a model sometimes emits before the actual post — e.g.
# "Here's the post:" or "Okay, let's do this.". Kept deliberately narrow so a
# legitimate post that happens to open this way is rarely touched.
_PREAMBLE_PATTERNS = (
    # "Here's the post:", "Here is your tweet:", "Here's a take:" — ends in colon.
    re.compile(r"^\s*here(?:'s| is)\b[^:\n]{0,40}:\s*", re.IGNORECASE),
    # Short filler openers ending in a period: "Sure.", "Okay, let's do this.".
    re.compile(
        r"^\s*(?:sure|certainly|okay|ok|alright|absolutely|of course|got it)\b"
        r"[^.\n]{0,40}\.\s+",
        re.IGNORECASE,
    ),
)


# ---------------------------------------------------------------------------
# Text post-processing
# ---------------------------------------------------------------------------


def html_to_text(raw: str | None) -> str:
    """Flatten HTML source text to clean plain text for the prompt CONTEXT.

    Strips tags, unescapes entities, and collapses whitespace. Applied centrally
    so every source (RSS, Reddit, Google News) feeds the model markup-free
    context rather than ``<a>``/``<img>`` noise and feed boilerplate.
    """
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = _html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def strip_preamble(text: str) -> str:
    """Drop a leading meta-clause a model prepends before the real post.

    Conservative: only known scaffolding phrases, and never returns empty — if
    stripping would leave nothing, the original is kept. Most useful for smaller
    local models that ignore the 'return only the post text' rule.
    """
    out = text.strip()
    for _ in range(3):  # peel a couple of stacked preambles ("Okay. Here's...:")
        for pat in _PREAMBLE_PATTERNS:
            stripped = pat.sub("", out, count=1).strip()
            if stripped != out and stripped:
                out = stripped
                break
        else:
            break
    return out


def sanitize_formatting(text: str) -> str:
    """Strip markdown and dash punctuation the model may add despite the prompt.

    A deterministic backstop for the 'plain text, no markdown, no dashes' rules:
    unwraps emphasis/code markdown, drops heading/bullet line markers, and turns
    em/en dashes and spaced hyphens (used as dashes) into commas. Intra-word
    hyphens like 'GPT-4' or 'e-commerce' are left intact on purpose so names and
    numbers aren't mangled.
    """
    text = _MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2), text)
    text = _MD_CODE_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_BULLET_RE.sub("", text)
    text = _DASH_RE.sub(", ", text)
    text = _SPACED_HYPHEN_RE.sub(", ", text)
    # Tidy up artifacts the comma substitutions can leave behind.
    text = _COMMA_FIXUP_RE.sub(",", text)
    text = _DOUBLE_COMMA_RE.sub(",", text)
    text = _MULTISPACE_RE.sub(" ", text)
    return text.strip()


def strip_wrapping_quotes(text: str) -> str:
    """Remove quotes/backticks the model wrapped the whole post in."""
    text = text.strip()
    if len(text) >= 2 and text[0] in "\"“”'`" and text[-1] in "\"“”'`":
        text = text[1:-1].strip()
    # Also peel stray leading/trailing quote runs.
    return _WRAP_RE.sub("", text).strip()


def effective_length(text: str) -> int:
    """Character count as X sees it: every URL counts as a flat 23 chars."""
    urls = _URL_RE.findall(text)
    raw = len(text)
    return raw - sum(len(u) for u in urls) + URL_WEIGHT * len(urls)


@dataclass
class IndependentConfig:
    enabled: bool = False
    per_day: int = 1
    types: list[str] = field(default_factory=lambda: ["take"])
    image: str = "ai"  # ai | none

    @classmethod
    def from_niche(cls, raw: dict) -> "IndependentConfig":
        block = (raw or {}).get("independent_take") or {}
        types = [t for t in (block.get("types") or ["take"]) if t in INDEPENDENT_ELIGIBLE]
        return cls(
            enabled=bool(block.get("enabled", False)),
            per_day=int(block.get("per_day", 1)),
            types=types or ["take"],
            image=str(block.get("image", "ai")),
        )


@dataclass
class DraftResult:
    post_type: str
    text: str
    media_url: str | None
    independent: bool
    title: str  # source title or topic, for CLI display


def _finalize_text(
    provider: TextProvider,
    *,
    config: dict,
    post_type: str,
    subject: str,
    body: str,
    char_limit: int,
) -> str:
    """Generate, strip quotes, and run one rewrite-to-fit pass if over limit.

    Raises :class:`TextProviderError` if the model returns nothing usable — we
    fail the draft loudly rather than storing an empty post or falling back.
    """
    system, user = build_messages(
        config=config, post_type=post_type, subject=subject, body=body,
        char_limit=char_limit,
    )
    text = sanitize_formatting(
        strip_wrapping_quotes(strip_preamble(provider.generate(system, user)))
    )
    if not text.strip():
        raise TextProviderError(
            f"text provider {provider.name!r} returned an empty response for "
            f"'{subject[:60]}'"
        )

    if effective_length(text) > char_limit:
        # One rewrite-to-fit pass, asking for a tighter draft.
        system, user = build_messages(
            config=config, post_type=post_type, subject=subject, body=body,
            char_limit=char_limit, shorten_to=char_limit - 10,
        )
        retry = sanitize_formatting(
            strip_wrapping_quotes(strip_preamble(provider.generate(system, user)))
        )
        if effective_length(retry) <= char_limit:
            text = retry
        else:
            # Last resort: hard trim at a word boundary so we never store an
            # over-limit draft.
            text = _hard_trim(retry or text, char_limit)
    return text


def _hard_trim(text: str, limit: int) -> str:
    if effective_length(text) <= limit:
        return text
    words = text.split()
    out = ""
    for w in words:
        nxt = (out + " " + w).strip()
        if effective_length(nxt + "…") > limit:
            break
        out = nxt
    return (out + "…") if out else text[: limit - 1] + "…"


def _attach_image(
    image_provider: ImageProvider,
    *,
    niche_name: str,
    subject: str,
    item_media: list[str] | None,
    text_provider: TextProvider | None = None,
    body: str = "",
):
    """Resolve an image from the niche's image source.

    Driven entirely by the niche-level ``image_source`` (which selects the
    provider) — there are no per-tone visual rules. Returns
    ``(url, attribution, provider_name)``.
    """
    if getattr(image_provider, "name", "") == "content":
        # Niche image source = "Content": use the item's own media, never generate.
        return (item_media[0], "source", "source") if item_media else (None, None, None)
    if getattr(image_provider, "name", "") != "unsplash":
        # Only Unsplash fetches a real image; NoneProvider yields none.
        return None, None, None
    # Unsplash searches a stock-photo library, so a concrete visual phrase beats
    # the raw headline. Let the model write one; fall back to the deterministic
    # keyword heuristic offline or on failure.
    query = None
    if text_provider is not None:
        query = choose_image_query(
            {}, text_provider=text_provider,
            niche_name=niche_name, subject=subject, body=body,
        )
    query = query or unsplash_query(niche_name=niche_name, subject=subject)
    result = image_provider.image_for(query)
    if result is None:
        return None, None, None
    return result.url, result.attribution, result.provider


# ---------------------------------------------------------------------------
# Generation entry points
# ---------------------------------------------------------------------------


def generate_for_niche(
    session: Session,
    niche_slug: str,
    config: dict,
    *,
    limit: int = 5,
    text_provider: TextProvider | None = None,
    image_provider: ImageProvider | None = None,
    persist: bool = True,
    rerank: bool = True,
    platform_account_id: str | None = None,
) -> list[DraftResult]:
    """Generate up to ``limit`` source-derived drafts for a niche.

    Walks the prioritized candidate queue, skipping items already drafted and
    types whose daily cap is spent. With ``persist=False`` nothing is written
    (used by the ``preview`` dry-run command). With ``rerank=True`` (default),
    an LLM importance pass reorders the top of the queue before drafting; it is
    a no-op offline, so ``preview`` can leave it on too.

    ``platform_account_id`` stamps each draft with the generating workspace so
    the queue and publishing stay isolated when several workspaces follow the
    same (shared) niche. Falls back to the niche config's legacy ``account_id``.
    """
    text_provider = text_provider or get_text_provider(config)
    image_provider = image_provider or get_image_provider(config)
    pt_cfg = PostTypesConfig.from_niche(config)
    niche_name = config.get("display_name") or niche_slug
    char_limit = int(config.get("char_limit", DEFAULT_CHAR_LIMIT))
    account_id = platform_account_id or niche_account_id(config)

    already = content_ids_with_posts(session, niche_slug)
    ranked = candidate_queue(session, niche_slug, config)
    if rerank:
        ranked = rerank_by_importance(
            ranked, config, text_provider=text_provider, niche_name=niche_name
        )
    new_status = initial_status(config)  # always "draft" (approval retired)

    # Tone is chosen per item by the content-aware classifier (choose_post_type).
    # The shuffled deck is the offline / on-failure fallback: it deals a reshuffled
    # set of enabled tones so a batch gets distinct tones before any repeat.
    tones = pt_cfg.enabled_tones()
    deck: list[str] = []

    drafts: list[DraftResult] = []
    for cand in ranked:
        if len(drafts) >= limit:
            break
        if cand.row.id in already:
            continue

        if not deck:
            deck = shuffled_deck(tones)
        # Content-aware tone: let the model pick the best-fitting enabled tone for
        # this item; fall back to the rotation deck offline or on failure.
        fallback = deck.pop()
        post_type = (
            choose_post_type(
                cand, config, text_provider=text_provider,
                choices=tones, niche_name=niche_name,
            )
            or fallback
        )

        subject = cand.row.title
        body = html_to_text(cand.row.summary or cand.row.body)[:1500]
        text = _finalize_text(
            text_provider, config=config, post_type=post_type,
            subject=subject, body=body, char_limit=char_limit,
        )
        media_url, attribution, img_provider = _attach_image(
            image_provider, niche_name=niche_name,
            subject=subject, item_media=cand.row.media_urls,
            text_provider=text_provider, body=body,
        )

        if persist:
            insert_generated_post(
                session,
                niche_slug=niche_slug,
                post_type=post_type,
                text=text,
                ai_text_provider=text_provider.name,
                content_item_id=cand.row.id,
                media_url=media_url,
                media_attribution=attribution,
                ai_image_provider=img_provider,
                priority_score=cand.priority_score,
                status=new_status,
                platform_account_id=account_id,
            )
        already.add(cand.row.id)
        drafts.append(
            DraftResult(
                post_type=post_type, text=text, media_url=media_url,
                independent=False, title=subject,
            )
        )

    if persist:
        session.commit()
    return drafts


def generate_independent(
    session: Session,
    niche_slug: str,
    config: dict,
    *,
    text_provider: TextProvider | None = None,
    image_provider: ImageProvider | None = None,
    topic: str | None = None,
    persist: bool = True,
    platform_account_id: str | None = None,
) -> list[DraftResult]:
    """Generate the niche's independent post(s) — e.g. the daily Take.

    These have no ``content_item_id``. The job is idempotent per day: it only
    tops up to ``per_day`` independent posts created today, so re-running won't
    flood the queue. ``topic`` overrides the niche/topic seed (otherwise the
    niche name is the subject and the persona supplies the angle).

    ``platform_account_id`` stamps the drafts and scopes the per-day idempotency
    to this workspace, so each workspace following a shared niche gets its own
    independent take. Falls back to the niche config's legacy ``account_id``.
    """
    cfg = IndependentConfig.from_niche(config)
    if not cfg.enabled:
        return []

    text_provider = text_provider or get_text_provider(config)
    image_provider = get_image_provider(config) if image_provider is None else image_provider
    niche_name = config.get("display_name") or niche_slug
    char_limit = int(config.get("char_limit", DEFAULT_CHAR_LIMIT))
    account_id = platform_account_id or niche_account_id(config)

    # How many independent posts already exist today (for this workspace)?
    made_today = _independent_count_today(session, niche_slug, account_id)
    remaining = max(0, cfg.per_day - made_today)
    if remaining == 0:
        return []

    # Shuffled rotation over the independent-eligible tones for this niche.
    tones = [t for t in cfg.types if t in INDEPENDENT_ELIGIBLE] or ["take"]
    deck: list[str] = []

    drafts: list[DraftResult] = []
    for _ in range(remaining):
        if not deck:
            deck = shuffled_deck(tones)
        post_type = deck.pop()

        subject = topic or f"an original {post_type} about {niche_name}"
        text = _finalize_text(
            text_provider, config=config, post_type=post_type,
            subject=subject, body="", char_limit=char_limit,
        )
        if cfg.image == "none":
            media_url = attribution = img_provider = None
        else:
            media_url, attribution, img_provider = _attach_image(
                image_provider, niche_name=niche_name,
                subject=text[:120], item_media=None,
                text_provider=text_provider,
            )

        if persist:
            insert_generated_post(
                session,
                niche_slug=niche_slug,
                post_type=post_type,
                text=text,
                ai_text_provider=text_provider.name,
                content_item_id=None,  # independent
                media_url=media_url,
                media_attribution=attribution,
                ai_image_provider=img_provider,
                status=initial_status(config),
                platform_account_id=account_id,
            )
        drafts.append(
            DraftResult(
                post_type=post_type, text=text, media_url=media_url,
                independent=True, title=subject,
            )
        )

    if persist:
        session.commit()
    return drafts


def _independent_count_today(
    session: Session, niche_slug: str, platform_account_id: str | None = None
) -> int:
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import func, select

    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    stmt = select(func.count()).where(
        GeneratedPost.niche_slug == niche_slug,
        GeneratedPost.content_item_id.is_(None),
        GeneratedPost.created_at >= start,
        GeneratedPost.created_at < end,
    )
    # Scope idempotency to the workspace so each gets its own daily take on a
    # shared niche; ``None`` keeps the legacy niche-wide behaviour.
    if platform_account_id is not None:
        stmt = stmt.where(
            GeneratedPost.platform_account_id == platform_account_id
        )
    return int(session.execute(stmt).scalar() or 0)
