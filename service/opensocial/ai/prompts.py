"""Prompt assembly: persona + post-type template + cross-cutting rules.

The persona comes from the niche config as three per-niche controls — a **style**
(casual / funny / informative / professional / question / supportive), a target
**length** (very_short … long), and free-text **instructions** — that the
dashboard's General tab edits. (A legacy free-text ``voice``/``tone`` is still
honored as a fallback when no style is set.) The post type selects an intent
template; and a fixed set of cross-cutting rules — absorbed from a working
reference implementation — is appended to every prompt so drafts come out
standalone and link-free regardless of niche or type.

``build_messages`` returns ``(system, user)`` for any ``TextProvider``. The
``user`` block always carries a ``SUBJECT:`` line, which the offline
:class:`~opensocial.ai.text.TemplateProvider` keys off so generation is
testable without a model.
"""

from __future__ import annotations

import re

# Per-type intent instructions. Keys match the taxonomy in project.md.
POST_TYPE_TEMPLATES: dict[str, str] = {
    "news": (
        "Write a NEWS post about a timely development. Lead with the "
        "implication or 'why it matters', not the bare headline."
    ),
    "spotlight": (
        "Write a SPOTLIGHT post highlighting a tool, repo, paper, or product "
        "worth knowing about, and what makes it interesting."
    ),
    "insight": (
        "Write an INSIGHT post: one non-obvious observation or synthesis that "
        "makes the reader think differently."
    ),
    "take": (
        "Write a TAKE: a bold, opinionated stance on this topic or trend. Have "
        "a clear point of view; don't hedge."
    ),
    "tip": (
        "Write a TIP: one specific, actionable piece of how-to advice the "
        "reader can use immediately."
    ),
    "question": (
        "Write a QUESTION post that provokes genuine debate and replies. End "
        "with an open question."
    ),
    "meme": (
        "Write a witty, relatable one-liner for this niche. The humor should "
        "land even before the image."
    ),
}

# Appended to every prompt. These serve the standalone, no-link design.
CROSS_CUTTING_RULES = (
    "Rules:\n"
    "- Write it as an original, standalone thought. Never reference or hint at "
    "a source: no 'according to', no outlet or account names, no 'saw this', "
    "no links or 'read more'.\n"
    "- Name the subject inside the post. Never open with 'this', 'that', "
    "'they', or 'it' pointing at something the reader can't see — the reader "
    "has no headline, link, or article in front of them, so state what you're "
    "talking about in your own words.\n"
    "- Open with a strong, scroll-stopping hook on the first line. No corporate "
    "filler like 'excited to share' or 'thrilled to announce'.\n"
    "- Plain text only. Do not wrap the post in quotes or backticks. No "
    "hashtags unless they are genuinely natural.\n"
    "- Never use markdown of any kind (no **bold**, *italics*, backticks, "
    "headings, or bullet points) and no dashes. Not even hyphens between words, "
    "not en dashes, not em dashes. Use a comma, a period, or a new sentence "
    "instead.\n"
    "- Use dead-simple words and short, plain sentences. Write so anyone can "
    "read it instantly; no jargon, no fancy or complex vocabulary.\n"
    "- Return only the post text, nothing else."
)


DEFAULT_VOICE = (
    "You are a sharp, knowledgeable voice in this niche who writes punchy, "
    "high-signal posts for X."
)

# Per-niche post style → a voice instruction. Keys match the dashboard buttons.
STYLE_PROMPTS: dict[str, str] = {
    "casual": (
        "Write in a casual, conversational voice — relaxed and friendly, like "
        "talking to a peer."
    ),
    "funny": (
        "Write with humor and wit. Be playful and entertaining; land a joke or "
        "a clever turn of phrase."
    ),
    "informative": (
        "Write in an informative, clear voice. Lead with substance and explain "
        "why it matters."
    ),
    "professional": (
        "Write in a professional, authoritative voice — polished, credible, and "
        "precise."
    ),
    "question": (
        "Frame the post to provoke curiosity and replies. Lean into open "
        "questions and discussion."
    ),
    "supportive": (
        "Write in a warm, encouraging, supportive voice that uplifts and "
        "motivates the reader."
    ),
}

# Per-niche target length → a length instruction. Keys match the dashboard.
LENGTH_PROMPTS: dict[str, str] = {
    "very_short": "Keep it extremely short — just 2-5 words, punchy.",
    "short": "Keep it to a single short sentence.",
    "medium": "Keep it to 1-2 lines.",
    "long": "Write 3-4 lines of substantive text.",
}


def _persona_block(config: dict | None) -> tuple[list[str], str, str | None, str]:
    """Resolve the per-niche persona controls into prompt fragments.

    Returns ``(voice_lines, display_name, length_line, instructions)``:

    * ``voice_lines`` — the style instruction (or a legacy free-text
      ``voice``/``tone`` fallback, or a default) as a list of system lines.
    * ``length_line`` — the target-length instruction, or ``None`` if unset.
    * ``instructions`` — free-text custom instructions for this niche ("").
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


def build_messages(
    *,
    config: dict | None,
    post_type: str,
    subject: str,
    body: str = "",
    char_limit: int = 280,
    shorten_to: int | None = None,
) -> tuple[str, str]:
    """Assemble the ``(system, user)`` prompt for one draft.

    ``subject`` is the topic line (a content item's title, or the take's topic
    for independent posts); ``body`` is optional supporting context. When
    ``shorten_to`` is set, a rewrite-to-fit instruction is added so a too-long
    first draft can be regenerated tighter.
    """
    voice_lines, niche_name, length_line, instructions = _persona_block(config)
    template = POST_TYPE_TEMPLATES.get(post_type, POST_TYPE_TEMPLATES["insight"])

    system_parts = [
        *voice_lines,
        f"You are posting for the '{niche_name}' niche.",
        template,
    ]
    if length_line is not None:
        system_parts.append(length_line)
    system_parts.append(
        f"Keep it under {char_limit} characters (every URL counts as 23)."
    )
    if shorten_to is not None:
        system_parts.append(
            f"The previous draft was too long. Shorten to {shorten_to} "
            "characters while keeping the hook and the point."
        )
    if instructions:
        system_parts.append(f"Additional instructions: {instructions}")
    system_parts.append(CROSS_CUTTING_RULES)

    user_parts = [f"SUBJECT: {subject}"]
    if body:
        user_parts.append(f"\nCONTEXT:\n{body.strip()}")
    return "\n\n".join(system_parts), "\n".join(user_parts)


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
    poorly or returns off-topic stock. We keep just a niche anchor (the niche's
    first word) plus the single most salient word of the subject — the longest
    non-stopword token, a decent proxy for the most specific/topical term — so the
    search stays tight and relevant. The provider broadens from here (see
    ``UnsplashProvider._query_attempts``) only if a tight query finds nothing.
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


def build_image_query_messages(
    *, niche_name: str, subject: str, body: str = ""
) -> tuple[str, str]:
    """Assemble the ``(system, user)`` prompt for the LLM image-query writer.

    Unsplash searches a *stock photo* library, so the best query names a concrete,
    photographable scene — not the headline, a brand, or an abstract concept. We
    ask the model for one short visual phrase that captures the post's topic; the
    caller (:func:`~opensocial.ai.ranking.choose_image_query`) cleans the reply and
    falls back to the deterministic :func:`unsplash_query` heuristic offline or on
    failure.
    """
    system = (
        f"You write image search queries for a stock photo library (Unsplash), "
        f"for posts in the '{niche_name}' niche.\n"
        "Given a post topic, return ONE short search query (2-4 words) naming a "
        "concrete, photographable scene or object that visually represents it.\n"
        "Use plain visual nouns. Do NOT use brand names, product names, people's "
        "names, dates, or abstract concepts — they return no good stock photos.\n"
        "Return ONLY the query words: no quotes, punctuation, or explanation."
    )
    user = f"Topic: {subject}"
    if body:
        user += f"\n{body.strip()[:200]}"
    return system, user


# One-line description of each tone, for the content-aware post-type picker.
TONE_GLOSS: dict[str, str] = {
    "news": "a timely, factual development worth reporting",
    "spotlight": "highlighting a specific tool, product, paper, or project",
    "insight": "a non-obvious observation or synthesis",
    "take": "a bold, opinionated stance on a topic or trend",
    "tip": "specific, actionable how-to advice",
    "question": "an open question that invites debate",
    "meme": "a witty, relatable one-liner",
}


def build_posttype_messages(
    *, niche_name: str, subject: str, body: str = "", choices: list[str]
) -> tuple[str, str]:
    """Assemble the ``(system, user)`` prompt for the post-type classifier.

    The model is shown the niche's enabled tones (with a one-line gloss each) and
    asked to return the single best-fitting one for the item — so the tag matches
    what the content actually is rather than a random rotation slot.
    """
    options = [c for c in choices if c in TONE_GLOSS] or list(choices)
    lines = "\n".join(f"- {c}: {TONE_GLOSS.get(c, c)}" for c in options)
    system = (
        f"You are an editor for the '{niche_name}' niche. Choose the single post "
        "format that best fits the item below.\n"
        f"Options:\n{lines}\n"
        "Judge by what the item actually is, not by variety. Return ONLY the one "
        "option word (e.g. 'take'), nothing else."
    )
    user = f"Item: {subject}"
    if body:
        user += f"\n{body.strip()[:200]}"
    return system, user


def build_ranking_messages(
    *, niche_name: str, items: list[str]
) -> tuple[str, str]:
    """Assemble the ``(system, user)`` prompt for the importance reranker.

    ``items`` is a list of ``"<title> — <summary>"`` strings; the model is asked
    to return their 1-based indices ordered most-important-first. Importance is
    framed as newsworthiness/significance for the niche — not mere recency.
    """
    system = (
        f"You are an editor for the '{niche_name}' niche deciding what is most "
        "worth posting about right now.\n"
        "Rank the numbered items by IMPORTANCE — newsworthiness, significance, "
        "and how much the audience would care — not by recency or how recent "
        "they sound.\n"
        "Return ONLY a comma-separated list of the item numbers, most important "
        "first, e.g. '3, 1, 2'. No prose, no explanations."
    )
    numbered = "\n".join(f"{i}. {text}" for i, text in enumerate(items, start=1))
    user = f"Items:\n{numbered}"
    return system, user
