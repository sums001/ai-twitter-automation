"""Write one post from the knowledge base: pick, gather facts, write, polish.

A post costs at most two LLM calls, usually one:

1. :func:`~opensocial.core.knowledge.shortlist` ranks the account's unused
   stories for free.
2. :func:`~opensocial.ai.ranking.pick_story` asks the model to choose only when
   the top two are close (~250 tokens).
3. The winning story's article is fetched and boiled down to a fact sheet
   (free), then one **write** call produces the post (~700 tokens in).
4. Code cleans the draft: markdown, dashes, hashtags, emoji, AI clichés and
   length. A rewrite call happens only when code can't fix it.

When every story is already used, a **synthesis take** reads the room across
the biggest recent stories instead (the account asked to always post). Only an
empty knowledge base yields no draft.
"""

from __future__ import annotations

import html as _html
import random
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from opensocial.ai.images import ImageProvider, get_image_provider
from opensocial.ai.prompts import SYNTHESIS_SHAPE, build_messages, unsplash_query
from opensocial.ai.ranking import is_offline, pick_story
from opensocial.ai.text import TextProvider, TextProviderError, get_text_provider
from opensocial.core.db import (
    ContentItemRow,
    GeneratedPost,
    Story,
    insert_generated_post,
)
from opensocial.core.knowledge import (
    clean_text,
    has_stories,
    ingest,
    mark_shown,
    mark_used,
    recent_post_lines,
    recent_top_stories,
    shortlist,
    story_items,
)
from opensocial.core.posttypes import PostTypesConfig

DEFAULT_CHAR_LIMIT = 280
URL_WEIGHT = 23  # t.co wraps every link to a flat 23 chars
_URL_RE = re.compile(r"https?://\S+")
# Wrapping quotes/backticks a model sometimes adds around the whole post.
_WRAP_RE = re.compile(r'^\s*["“”\'`]+|["“”\'`]+\s*$')

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Markdown + dash punctuation a model may add despite the prompt. Code removes
# them deterministically, so the prompt doesn't spend tokens forbidding them.
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_MD_CODE_RE = re.compile(r"`+([^`]+)`+")
_MD_ITALIC_RE = re.compile(r"(?<!\w)[*_](\S.*?\S|\S)[*_](?!\w)")
_MD_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+", re.MULTILINE)
_MD_BULLET_RE = re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE)
# Every Unicode "dash punctuation" variant a model may emit as a sentence break.
# ASCII '-' is excluded so intra-word hyphens (GPT-4) survive; spaced ASCII
# dashes are handled by _SPACED_HYPHEN_RE.
_DASH_RE = re.compile(
    "\\s*["
    "‒–—―"  # figure, en, em dash, horizontal bar
    "−"  # minus sign
    "⸺⸻"  # two-em, three-em dash
    "﹘﹣－"  # small em dash, small/fullwidth hyphen-minus
    "]\\s*"
)
_SPACED_HYPHEN_RE = re.compile(r"[ \t]+-[ \t]+")  # " - " used as a dash → comma
_COMMA_FIXUP_RE = re.compile(r"[ \t]+,")
_DOUBLE_COMMA_RE = re.compile(r",\s*,")
_MULTISPACE_RE = re.compile(r"[ \t]{2,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+\n")
_MANY_NEWLINES_RE = re.compile(r"\n{3,}")

# Leading "scaffolding" a model sometimes emits before the actual post.
_PREAMBLE_PATTERNS = (
    re.compile(r"^\s*here(?:'s| is)\b[^:\n]{0,40}:\s*", re.IGNORECASE),
    re.compile(
        r"^\s*(?:sure|certainly|okay|ok|alright|absolutely|of course|got it)\b"
        r"[^.\n]{0,40}\.\s+",
        re.IGNORECASE,
    ),
)

_HASHTAG_TAIL_RE = re.compile(r"(?:\s*#\w+)+\s*$")
_HASHTAG_INLINE_RE = re.compile(r"(?<!\w)#([A-Za-z]\w*)")
_EMOJI_RE = re.compile(
    "["
    "\U0001F300-\U0001FAFF"  # symbols, pictographs, emoticons, transport…
    "\U0001F000-\U0001F2FF"  # mahjong, cards, enclosed alphanumerics
    "\U00002600-\U000027BF"  # misc symbols, dingbats
    "\U0001F1E6-\U0001F1FF"  # flags
    "️‍"  # variation selector, zero-width joiner
    "]+"
)

# Whole sentences that are pure AI filler: dropped outright.
_FILLER_SENTENCES = {
    "let that sink in", "buckle up", "the future is here", "stay tuned",
    "thoughts", "what do you think", "just saying", "think about that",
    "read that again", "this is huge", "this changes everything",
    "the implications are huge", "the implications are massive",
    "game changer", "a game changer", "exciting times", "wild times",
    "what a time to be alive", "the race is on", "the stakes are high",
}
# Cliché words with a plain replacement code can apply safely.
_CLICHE_REPLACEMENTS = [
    (re.compile(r"\bgame[- ]chang(?:er|ing)\b", re.IGNORECASE), "big deal"),
    (re.compile(r"\brevolutioniz(?:e|es|ing)\b", re.IGNORECASE), "change"),
    (re.compile(r"\bdelv(?:e|es|ing) into\b", re.IGNORECASE), "dig into"),
    (re.compile(r"\butiliz(?:e|es|ing)\b", re.IGNORECASE), "use"),
    (re.compile(r"\bleverag(?:e|es|ing)\b", re.IGNORECASE), "use"),
    (re.compile(r"\bharness(?:es|ing)?\b", re.IGNORECASE), "use"),
    (re.compile(r"\bseamless(ly)?\b", re.IGNORECASE), "smooth\\1"),
    (re.compile(r"\bunleash(?:es|ing|ed)?\b", re.IGNORECASE), "release"),
    (re.compile(r",?\s*in today's (?:fast[- ]paced|ever[- ]changing|digital) world,?", re.IGNORECASE), ""),
]
# Clichés with no safe mechanical fix: trigger one rewrite call.
_CLICHE_WORDS = (
    "tapestry", "testament to", "paradigm", "realm", "ever-evolving",
    "landscape", "navigate the", "it's not just", "here's why", "here's the thing",
    "the real question is", "underscores", "a new era",
    # stock "AI poster" moves: formula openers and bolted-on punchlines
    "firmly in the", "than most people realize", "massively underestimate",
    "nobody is talking about", "let that sink in", "plot twist", "buckle up",
    "my practical takeaway", "that's the kind of", "cosplay", "with consequences",
    "like a dare", "turns out", "at this point, ",
)


# ---------------------------------------------------------------------------
# Text post-processing
# ---------------------------------------------------------------------------


def html_to_text(raw: str | None) -> str:
    """Flatten HTML source text to clean plain text."""
    if not raw:
        return ""
    text = _TAG_RE.sub(" ", raw)
    text = _html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def strip_preamble(text: str) -> str:
    """Drop a leading meta-clause ("Here's the post:") a model prepends."""
    out = text.strip()
    for _ in range(3):
        for pat in _PREAMBLE_PATTERNS:
            stripped = pat.sub("", out, count=1).strip()
            if stripped != out and stripped:
                out = stripped
                break
        else:
            break
    return out


def sanitize_formatting(text: str) -> str:
    """Strip markdown and dash punctuation; keep line breaks (they're voice).

    Intra-word hyphens like 'GPT-4' or 'e-commerce' are left intact.
    """
    text = _MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2), text)
    text = _MD_CODE_RE.sub(r"\1", text)
    text = _MD_ITALIC_RE.sub(r"\1", text)
    text = _MD_HEADING_RE.sub("", text)
    text = _MD_BULLET_RE.sub("", text)
    text = _DASH_RE.sub(", ", text)
    text = _SPACED_HYPHEN_RE.sub(", ", text)
    text = _COMMA_FIXUP_RE.sub(",", text)
    text = _DOUBLE_COMMA_RE.sub(",", text)
    text = _MULTISPACE_RE.sub(" ", text)
    text = _TRAILING_SPACE_RE.sub("\n", text)
    text = _MANY_NEWLINES_RE.sub("\n\n", text)
    return text.strip()


def strip_wrapping_quotes(text: str) -> str:
    """Remove quotes/backticks the model wrapped the whole post in."""
    text = text.strip()
    if len(text) >= 2 and text[0] in "\"“”'`" and text[-1] in "\"“”'`":
        text = text[1:-1].strip()
    return _WRAP_RE.sub("", text).strip()


def strip_hashtags_and_emoji(text: str) -> str:
    """Drop a trailing hashtag block, unhash inline tags, remove emoji."""
    text = _HASHTAG_TAIL_RE.sub("", text)
    text = _HASHTAG_INLINE_RE.sub(r"\1", text)
    text = _EMOJI_RE.sub("", text)
    return _MULTISPACE_RE.sub(" ", text).strip()


def _sentences(paragraph: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", paragraph.strip()) if s]


def fix_cliches(text: str) -> tuple[str, list[str]]:
    """Remove filler sentences and swap cliché words; report what's left.

    Returns ``(text, leftovers)``: clichés code couldn't fix, which justify one
    rewrite call.
    """
    paragraphs = []
    for para in text.split("\n\n"):
        kept = []
        for sentence in _sentences(para):
            key = re.sub(r"[^a-z' ]", "", sentence.lower()).strip()
            if key in _FILLER_SENTENCES:
                continue
            kept.append(sentence)
        if kept:
            paragraphs.append(" ".join(kept))
    out = "\n\n".join(paragraphs) if paragraphs else text
    for pat, repl in _CLICHE_REPLACEMENTS:
        out = pat.sub(repl, out)
    out = _MULTISPACE_RE.sub(" ", out).strip()
    lower = out.lower()
    leftovers = [w for w in _CLICHE_WORDS if w in lower]
    return out, leftovers


def effective_length(text: str) -> int:
    """Character count as X sees it: every URL counts as a flat 23 chars."""
    urls = _URL_RE.findall(text)
    return len(text) - sum(len(u) for u in urls) + URL_WEIGHT * len(urls)


def fit_length(text: str, limit: int) -> tuple[str, bool]:
    """Trim at sentence boundaries to fit ``limit``.

    Repeatedly drops the last sentence of the longest multi-sentence paragraph,
    then whole trailing paragraphs. Returns ``(text, needs_rewrite)``; a rewrite
    is needed when it still doesn't fit or the trim gutted the post.
    """
    if effective_length(text) <= limit:
        return text, False
    original = len(text)
    paras = [_sentences(p) for p in text.split("\n\n") if p.strip()]

    def joined() -> str:
        return "\n\n".join(" ".join(p) for p in paras if p)

    while effective_length(joined()) > limit:
        multi = [i for i, p in enumerate(paras) if len(p) > 1]
        if multi:
            i = max(multi, key=lambda k: len(" ".join(paras[k])))
            paras[i].pop()
        elif len(paras) > 1:
            paras.pop()
        else:
            break
    out = joined()
    if effective_length(out) > limit or len(out) < 0.45 * original:
        return text, True
    return out, False


def _hard_trim(text: str, limit: int) -> str:
    if effective_length(text) <= limit:
        return text
    out = ""
    for w in text.split(" "):
        nxt = (out + " " + w) if out else w
        if effective_length(nxt + "…") > limit:
            break
        out = nxt
    return (out.rstrip() + "…") if out else text[: limit - 1] + "…"


def polish(raw: str, char_limit: int) -> tuple[str, list[str], bool]:
    """All the free cleanup. Returns ``(text, cliché_leftovers, too_long)``."""
    text = sanitize_formatting(strip_wrapping_quotes(strip_preamble(raw or "")))
    text = strip_hashtags_and_emoji(text)
    text, leftovers = fix_cliches(text)
    text, too_long = fit_length(text, char_limit)
    return text, leftovers, too_long


def write_text(
    provider: TextProvider,
    *,
    config: dict,
    post_type: str,
    subject: str,
    facts: str = "",
    char_limit: int = DEFAULT_CHAR_LIMIT,
    samples: list[str] | None = None,
    shape: str | None = None,
) -> str:
    """One write call, free polish, and a rewrite only if code can't fix it.

    Raises :class:`TextProviderError` if the model returns nothing usable.
    """
    kwargs = dict(
        config=config, post_type=post_type, subject=subject, facts=facts,
        char_limit=char_limit, samples=samples, shape=shape,
    )
    system, user = build_messages(**kwargs)
    text, leftovers, too_long = polish(provider.generate(system, user), char_limit)
    if not text.strip():
        raise TextProviderError(
            f"text provider {provider.name!r} returned an empty response for "
            f"'{subject[:60]}'"
        )
    if (leftovers or too_long) and not is_offline(provider):
        system, user = build_messages(
            **kwargs,
            shorten_to=(char_limit - 20) if too_long else None,
            avoid=leftovers or None,
        )
        retry, retry_left, retry_long = polish(provider.generate(system, user), char_limit)
        if retry.strip() and (not retry_long or too_long):
            text, too_long = retry, retry_long
    if effective_length(text) > char_limit:
        text = _hard_trim(text, char_limit)
    return text


# ---------------------------------------------------------------------------
# Choosing a tone, gathering facts, attaching an image
# ---------------------------------------------------------------------------

# How often each tone is dealt, when the niche enables it. Weighted toward the
# shapes that read most human (a fact plus a reaction, a take, a musing).
TONE_WEIGHTS = {
    "news": 3.0, "take": 3.0, "insight": 2.0, "question": 1.0,
    "spotlight": 1.0, "tip": 0.5, "meme": 0.5,
}


def choose_tone(
    config: dict, *, last_tone: str | None = None, rng: random.Random | None = None
) -> str:
    """A weighted random tone from the niche's enabled set, not repeating the last."""
    enabled = PostTypesConfig.from_niche(config).enabled_tones()
    pool = [t for t in enabled if t != last_tone] or enabled
    weights = [TONE_WEIGHTS.get(t, 1.0) for t in pool]
    return (rng or random).choices(pool, weights=weights, k=1)[0]


def _last_tone(session: Session, workspace_id: str | None) -> str | None:
    stmt = select(GeneratedPost.post_type).order_by(GeneratedPost.created_at.desc()).limit(1)
    if workspace_id is not None:
        stmt = stmt.where(GeneratedPost.platform_account_id == workspace_id)
    row = session.execute(stmt).first()
    return row[0] if row else None


def gather_facts(
    session: Session, story: Story, *, fetch_article: bool = True
) -> tuple[str, bool]:
    """Fact sheet for a story: feed blurbs plus the fetched article's key lines.

    Returns ``(facts, used_article)``.
    """
    from opensocial.ai.article import fetch_first_article
    from opensocial.ai.factsheet import build_factsheet

    items = story_items(session, story)
    summaries: list[str] = []
    if story.summary:
        summaries.append(story.summary)
    for item in items:
        blurb = clean_text(item.summary or item.body)[:400]
        if blurb and blurb not in summaries and len(summaries) < 3:
            summaries.append(blurb)
    article = (
        fetch_first_article([i.url for i in items]) if fetch_article and items else None
    )
    facts = build_factsheet(
        title=story.title, article=article, summaries=summaries,
        entities=story.entities,
    )
    return facts, bool(article)


def _story_media(session: Session, story: Story) -> list[str]:
    for item in story_items(session, story):
        if item.media_urls:
            return list(item.media_urls)
    return []


def _attach_image(
    image_provider: ImageProvider,
    *,
    niche_name: str,
    subject: str,
    item_media: list[str] | None,
):
    """Resolve an image from the niche's image source (no LLM call).

    ``content`` passes the source item's own media through; ``unsplash`` runs a
    free keyword query; anything else attaches nothing. Returns
    ``(url, attribution, provider_name)``.
    """
    name = getattr(image_provider, "name", "")
    if name == "content":
        return (item_media[0], "source", "source") if item_media else (None, None, None)
    if name != "unsplash":
        return None, None, None
    result = image_provider.image_for(unsplash_query(niche_name=niche_name, subject=subject))
    if result is None:
        return None, None, None
    return result.url, result.attribution, result.provider


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


@dataclass
class DraftResult:
    text: str
    post_type: str
    niche_slug: str
    title: str
    story_id: str | None = None
    media_url: str | None = None
    post_id: str | None = None
    # "score" (free ranking decided), "ai" (pick call), or "synthesis".
    picked_by: str = "score"
    used_article: bool = False
    outlets: list[str] = field(default_factory=list)


def resolve_samples(config: dict, workspace_samples: list[str] | None) -> list[str] | None:
    """Voice samples for a niche: its own override, else the account's."""
    own = (config.get("persona") or {}).get("examples")
    if isinstance(own, list) and any(str(s).strip() for s in own):
        return [str(s) for s in own]
    return workspace_samples


def _niche_config(niche, ai_config: dict | None, unsplash_key: str | None) -> dict:
    config = dict(niche.raw)
    if ai_config is not None:
        config["ai"] = ai_config
    if unsplash_key:
        config["unsplash_access_key"] = unsplash_key
    return config


def draft_next(
    session: Session,
    workspace_id: str | None,
    niches: list,
    *,
    ai_config: dict | None = None,
    unsplash_key: str | None = None,
    voice_samples: list[str] | None = None,
    persist: bool = True,
    text_provider: TextProvider | None = None,
    image_provider: ImageProvider | None = None,
    fetch_article: bool | None = None,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> DraftResult | None:
    """Write the account's next post from the knowledge base.

    ``niches`` are the workspace's active :class:`NicheConfig` objects. With
    ``persist`` the draft is stored and its story marked used for this account
    (and the shortlisted losers marked shown); without it nothing is written
    (the ``preview`` command). Returns ``None`` only when the knowledge base has
    nothing at all for these niches.

    ``fetch_article`` defaults to on for real models and off for the offline
    template provider (which ignores facts anyway, and keeps tests offline).
    """
    now = now or datetime.now(timezone.utc)
    by_slug = {n.slug: n for n in niches}
    if not by_slug:
        return None
    slugs = list(by_slug)

    # Fold any not-yet-grouped items in first (idempotent and cheap), so content
    # stored before the knowledge base existed, or by a bare CLI fetch, counts.
    ingest(session, now=now)

    first = niches[0]
    probe_config = _niche_config(first, ai_config, unsplash_key)
    text_provider = text_provider or get_text_provider(probe_config)
    if fetch_article is None:
        fetch_article = not is_offline(text_provider)

    short = shortlist(session, workspace_id, slugs, now=now)
    if short:
        winner, how = pick_story(
            short,
            text_provider=text_provider,
            recent_posts=recent_post_lines(session, workspace_id),
            niche_names=[by_slug[s].display_name for s in slugs],
        )
        story = winner.story
        niche = by_slug[winner.niche_slug]
        config = _niche_config(niche, ai_config, unsplash_key)
        tone = choose_tone(config, last_tone=_last_tone(session, workspace_id), rng=rng)
        facts, used_article = gather_facts(session, story, fetch_article=fetch_article)
        text = write_text(
            text_provider, config=config, post_type=tone, subject=story.title,
            facts=facts, char_limit=int(config.get("char_limit", DEFAULT_CHAR_LIMIT)),
            samples=resolve_samples(config, voice_samples),
        )
        image_provider = image_provider or get_image_provider(config)
        media_url, attribution, img_provider = _attach_image(
            image_provider, niche_name=niche.display_name, subject=story.title,
            item_media=_story_media(session, story),
        )
        result = DraftResult(
            text=text, post_type=tone, niche_slug=niche.slug, title=story.title,
            story_id=story.id, media_url=media_url, picked_by=how,
            used_article=used_article, outlets=list(story.outlets or []),
        )
        if persist:
            post = insert_generated_post(
                session,
                niche_slug=niche.slug,
                post_type=tone,
                text=text,
                ai_text_provider=text_provider.name,
                content_item_id=story.best_item_id,
                story_id=story.id,
                media_url=media_url,
                media_attribution=attribution,
                ai_image_provider=img_provider,
                priority_score=winner.score,
                platform_account_id=workspace_id,
            )
            mark_used(session, workspace_id, story.id, generated_post_id=post.id, now=now)
            mark_shown(
                session, workspace_id,
                [s.story.id for s in short[:3] if s.story.id != story.id], now=now,
            )
            session.commit()
            result.post_id = post.id
        return result

    # Nothing unused left: read the room across the biggest recent stories.
    if not has_stories(session, slugs, now=now):
        return None
    top = recent_top_stories(session, slugs, now=now, n=3)
    return _synthesis(
        session, workspace_id, top, by_slug, ai_config=ai_config,
        unsplash_key=unsplash_key, voice_samples=voice_samples, persist=persist,
        text_provider=text_provider, now=now,
    )


def _synthesis_facts(stories: list[Story]) -> str:
    lines = []
    for s in stories:
        blurb = (s.summary or "").split(". ")[0][:220]
        lines.append(f"- {s.title}" + (f": {blurb}" if blurb else ""))
    return "\n".join(lines)


def _synthesis(
    session, workspace_id, stories, by_slug, *, ai_config, unsplash_key,
    voice_samples, persist, text_provider, now,
) -> DraftResult:
    counts: dict[str, int] = {}
    for s in stories:
        for slug in (s.niche_relevance or {}):
            if slug in by_slug:
                counts[slug] = counts.get(slug, 0) + 1
    niche = by_slug[max(counts, key=counts.get)] if counts else next(iter(by_slug.values()))
    config = _niche_config(niche, ai_config, unsplash_key)
    subject = f"what the latest {niche.display_name} news adds up to"
    text = write_text(
        text_provider, config=config, post_type="take", subject=subject,
        facts=_synthesis_facts(stories),
        char_limit=int(config.get("char_limit", DEFAULT_CHAR_LIMIT)),
        samples=resolve_samples(config, voice_samples), shape=SYNTHESIS_SHAPE,
    )
    result = DraftResult(
        text=text, post_type="take", niche_slug=niche.slug, title=subject,
        picked_by="synthesis",
    )
    if persist:
        post = insert_generated_post(
            session, niche_slug=niche.slug, post_type="take", text=text,
            ai_text_provider=text_provider.name, platform_account_id=workspace_id,
        )
        session.commit()
        result.post_id = post.id
    return result


def regenerate_text(
    session: Session,
    post: GeneratedPost,
    config: dict,
    *,
    voice_samples: list[str] | None = None,
    text_provider: TextProvider | None = None,
    image_provider: ImageProvider | None = None,
) -> None:
    """Rewrite a draft in place from the same story, in a different tone.

    Uses the draft's story (re-reading its facts), a legacy draft's content
    item, or, for a synthesis take, the niche's current top stories.
    """
    text_provider = text_provider or get_text_provider(config)
    image_provider = image_provider or get_image_provider(config)
    niche_name = config.get("display_name") or post.niche_slug
    char_limit = int(config.get("char_limit", DEFAULT_CHAR_LIMIT))
    samples = resolve_samples(config, voice_samples)
    offline = is_offline(text_provider)

    story = session.get(Story, post.story_id) if post.story_id else None
    item = session.get(ContentItemRow, post.content_item_id) if post.content_item_id else None
    shape = None
    media: list[str] = []
    if story is not None:
        tone = choose_tone(config, last_tone=post.post_type)
        subject = story.title
        facts, _ = gather_facts(session, story, fetch_article=not offline)
        media = _story_media(session, story)
    elif item is not None:
        tone = choose_tone(config, last_tone=post.post_type)
        subject = clean_text(item.title)
        facts = clean_text(item.summary or item.body)[:800]
        media = list(item.media_urls or [])
    else:
        tone, shape = "take", SYNTHESIS_SHAPE
        top = recent_top_stories(session, [post.niche_slug], n=3)
        subject = f"what the latest {niche_name} news adds up to"
        facts = _synthesis_facts(top)

    post.text = write_text(
        text_provider, config=config, post_type=tone, subject=subject, facts=facts,
        char_limit=char_limit, samples=samples, shape=shape,
    )
    post.post_type = tone
    media_url, attribution, img_provider = _attach_image(
        image_provider, niche_name=niche_name, subject=subject, item_media=media,
    )
    post.media_url = media_url
    post.media_attribution = attribution
    post.ai_image_provider = img_provider
    post.ai_text_provider = text_provider.name
