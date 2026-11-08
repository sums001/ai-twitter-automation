"""Prompt assembly for the two LLM calls a post costs: *pick* and *write*.

The **write** prompt is deliberately short (~700 tokens with facts):

* the niche persona (style / length / custom instructions, as edited on the
  Niches page; a legacy free-text ``voice``/``tone`` still works),
* the account's **voice samples**, real posts the model imitates for rhythm and
  formatting (style, never topic),
* one post **shape** (what kind of post to write, chosen by code),
* four rules, and the story's facts.

Rules that code enforces anyway (no markdown, no dashes, length trim, hashtag
and emoji stripping) are *not* spelled out to the model; ``core/generate.py``
cleans the draft afterwards. The prompt text itself avoids dashes too, since
models copy the punctuation they are shown.

``build_messages`` returns ``(system, user)``. The ``user`` block always starts
with a ``SUBJECT:`` line, which the offline
:class:`~opensocial.ai.text.TemplateProvider` keys off, so generation is
testable without a model.
"""

from __future__ import annotations

import re

# Post shapes, one per tone. Keys are the tone taxonomy shown in the dashboard
# (``core/posttypes.py``); values say what kind of post to write. Written to
# match how people actually post, not how outlets headline.
POST_TYPE_TEMPLATES: dict[str, str] = {
    # Every shape starts from what happened: a reader scrolling past has not
    # seen the story, so a reaction without the news reads as nonsense.
    "news": (
        "Shape: say what happened in one plain sentence, then a blank line, then "
        "your honest reaction in a sentence."
    ),
    "take": (
        "Shape: say what happened in plain words, then your honest opinion on it "
        "in a sentence. Only an opinion the facts actually support."
    ),
    "insight": (
        "Shape: say what happened, then one thing it means or where it could "
        "lead. A short thought, not a report."
    ),
    "question": (
        "Shape: share the news in a line, then ask the one question you actually "
        "wonder about it."
    ),
    "spotlight": (
        "Shape: say what this is in plain words and why it caught your eye."
    ),
    "tip": (
        "Shape: say what happened, then the one practical thing someone in this "
        "space should take from it."
    ),
    "meme": (
        "Shape: a dry one liner about it that still makes clear what happened."
    ),
}

# Shape for the synthesis take (nothing fresh left, so read the room instead).
SYNTHESIS_SHAPE = (
    "Shape: read the room. Say what these developments together tell you about "
    "where things are heading right now. Two or three short paragraphs, first "
    "person, with a clear opinion."
)

RULES = (
    "Rules:\n"
    "- The reader has not seen any article. Make clear what happened and who did it, "
    "in words a smart person outside the field gets. Swap jargon for plain words.\n"
    "- React only to what the facts say. Never invent numbers, claims or a stance "
    "the facts do not support.\n"
    "- Sound like a person, not a news outlet or a brand. Plain words, short sentences.\n"
    "- No closing punchline, metaphor or wordplay. End on the point.\n"
    "- Never mention sources, outlets, links or 'according to'. No hashtags or emoji.\n"
    "- Return only the post text."
)

DEFAULT_VOICE = (
    "You are a sharp, well informed person in this niche who posts on X in "
    "your own voice."
)

# Per-niche post style → a voice instruction. Keys match the dashboard buttons.
STYLE_PROMPTS: dict[str, str] = {
    "casual": "Voice: casual and conversational, like talking to a peer.",
    "funny": (
        "Voice: light and dry. Humor only where it comes naturally from the facts; "
        "never bolt a joke onto the end."
    ),
    "informative": "Voice: clear and informative. Lead with substance and why it matters.",
    "professional": "Voice: professional and precise, credible without being stiff.",
    "question": "Voice: curious. Lean into open questions that invite replies.",
    "supportive": "Voice: warm and encouraging.",
}

# Per-niche target length → a length instruction. Keys match the dashboard.
LENGTH_PROMPTS: dict[str, str] = {
    "very_short": "Length: extremely short, a handful of words.",
    "short": "Length: a single short sentence.",
    "medium": "Length: one or two short lines.",
    "long": "Length: three or four short lines.",
}

# Voice samples used when an account hasn't saved its own: the style the
# account owner asked for (short paragraphs, first person, a fact then a
# reaction, the odd CAPS word, no hashtags or links).
DEFAULT_VOICE_SAMPLES: list[str] = [
    "Anthropic's UNRELEASED MODEL helped researchers solve an 18 year old "
    "conjecture in quantum computing.\n\nit's interesting how far ahead these "
    "internal models are compared to what the public has available.",
    "I would like to see what a model without Chinese and American "
    "characteristics would be like",
    "The more I use GPT-6.1 Terra, the more I like it. There, I said it.\n\n"
    "It's fast and really really strong in computer/browser use.\n\n"
    "I underestimated the use case for Terra.",
    "Now would be the best time for any AI startup to release something.\n\n"
    "Everyone is eagerly awaiting the next big release, yet the frontier labs "
    "are holding back.\n\nRight now, any release would receive far more "
    "attention than usual.",
    "Dario and team have actively put in fake tool calls to throw off "
    "distillers, it would be of NO surprise to see reasoning traces be filled "
    "with harmful and downright wrong choices.\n\nEvery time i read them, I "
    "panic because of how dumb opus is acting, but then the output is good.",
]

# Cap on how much sample text goes into a prompt (tokens cost money).
_SAMPLES_CHAR_BUDGET = 1100


def _persona_block(config: dict | None) -> tuple[list[str], str, str | None, str]:
    """Resolve the per-niche persona controls into prompt fragments.

    Returns ``(voice_lines, display_name, length_line, instructions)``.
    """
    raw = config or {}
    persona = raw.get("persona") or {}
    name = raw.get("display_name") or raw.get("slug") or "this niche"

    lines: list[str] = []
    style = (persona.get("style") or "").strip().lower()
    if style in STYLE_PROMPTS:
        lines.append(STYLE_PROMPTS[style])
    else:
        # Back-compat: honor a legacy free-text voice/tone when no style is set.
        voice = (persona.get("voice") or persona.get("prompt") or "").strip()
        if voice:
            lines.append(voice)
            tone = (persona.get("tone") or "").strip()
            if tone:
                lines.append(f"Tone: {tone}")
        else:
            lines.append(DEFAULT_VOICE)

    length_line = LENGTH_PROMPTS.get((persona.get("length") or "").strip().lower())
    instructions = (persona.get("instructions") or "").strip()
    return lines, name, length_line, instructions


def select_samples(samples: list[str] | None, budget: int = _SAMPLES_CHAR_BUDGET) -> list[str]:
    """Clean the samples and keep as many as fit the character budget."""
    out: list[str] = []
    used = 0
    for raw in samples or []:
        text = (raw or "").strip()
        if not text:
            continue
        if out and used + len(text) > budget:
            break
        out.append(text)
        used += len(text)
    return out


def _samples_block(samples: list[str] | None) -> str | None:
    chosen = select_samples(samples)
    if not chosen:
        return None
    body = "\n---\n".join(chosen)
    return (
        "Posts this account wrote. Match their voice, rhythm, casing and line "
        "breaks. Do not copy their topics or wording:\n---\n" + body + "\n---"
    )


def build_messages(
    *,
    config: dict | None,
    post_type: str,
    subject: str,
    facts: str = "",
    char_limit: int = 280,
    samples: list[str] | None = None,
    shorten_to: int | None = None,
    avoid: list[str] | None = None,
    shape: str | None = None,
) -> tuple[str, str]:
    """Assemble the ``(system, user)`` prompt for one post.

    ``subject`` is the story headline (or a synthesis topic); ``facts`` the
    extracted fact sheet. ``shape`` overrides the tone's shape (the synthesis
    take). ``shorten_to`` and ``avoid`` are only set on the rare rewrite pass,
    when code couldn't fix length or clichés on its own.
    """
    voice_lines, niche_name, length_line, instructions = _persona_block(config)
    shape_line = shape or POST_TYPE_TEMPLATES.get(post_type, POST_TYPE_TEMPLATES["take"])

    system_parts = [f"You write posts for an X account about {niche_name}.", *voice_lines]
    if length_line is not None:
        system_parts.append(length_line)
    block = _samples_block(samples)
    if block:
        system_parts.append(block)
    system_parts.append(shape_line)
    if instructions:
        system_parts.append(f"Additional instructions: {instructions}")
    system_parts.append(RULES)
    limit = shorten_to or char_limit
    system_parts.append(f"Keep it under {limit} characters.")
    if shorten_to is not None:
        system_parts.append(
            f"Your last draft was too long. Shorten to {shorten_to} characters, "
            "keeping the point."
        )
    if avoid:
        system_parts.append("Do not use these words or phrases: " + ", ".join(avoid) + ".")

    user_parts = [f"SUBJECT: {subject}"]
    if facts:
        user_parts.append(f"\nFACTS:\n{facts.strip()}")
    return "\n\n".join(system_parts), "\n".join(user_parts)


def build_pick_messages(
    *, niche_names: list[str], stories: list[str], recent_posts: list[str]
) -> tuple[str, str]:
    """The tiny "pick" prompt: which shortlisted story to post next.

    ``stories`` are one-line labels (headline plus outlet count). Only called
    when the free ranking has no clear winner, so most posts skip it.
    """
    topics = ", ".join(niche_names) or "this niche"
    system = (
        f"You choose what an X account about {topics} posts next. Pick the story "
        "that would make the most interesting post right now: real news people "
        "will talk about, not promos, listicles or stock tips, and not a repeat "
        "of the account's recent posts. Reply with the number only."
    )
    parts = []
    if recent_posts:
        parts.append("Recent posts:\n" + "\n".join(f"- {p}" for p in recent_posts))
    parts.append(
        "Stories:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(stories, start=1))
    )
    return system, "\n\n".join(parts)


# Common words that add no value to an Unsplash keyword search.
_QUERY_STOPWORDS = frozenset(
    "a an the of to in on for and or but with without as at by from is are was "
    "were be been being this that these those it its into over under after "
    "before how why what when who will would can could should new amid via".split()
)
_WORD_RE = re.compile(r"[A-Za-z0-9']+")


def unsplash_query(*, niche_name: str, subject: str, max_words: int = 2) -> str:
    """Build a tight, on-topic Unsplash query (``max_words`` words, default 2).

    Unsplash does literal, AND-style keyword matching, so a long headline matches
    poorly. Keeps a niche anchor (the niche's first word) plus the most salient
    word of the subject (the longest non-stopword token). Free: no LLM call.
    """
    anchor = (niche_name or "").split()
    anchor = anchor[0] if anchor else ""
    words = _WORD_RE.findall(subject or "")
    content = [w for w in words if w.lower() not in _QUERY_STOPWORDS and len(w) >= 2]
    top = max(content, key=len) if content else ""

    out: list[str] = []
    seen: set[str] = set()
    for part in (anchor, top):
        if part and part.lower() not in seen:
            seen.add(part.lower())
            out.append(part)
    return " ".join(out[:max_words]) or (subject or "").strip()
