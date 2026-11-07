"""Turn an article into a short fact sheet for the writer prompt (no LLM).

Sending a whole article costs thousands of tokens; most of it is background.
:func:`build_factsheet` keeps the sentences that carry the story's specifics,
those with figures, names from the headline, and headline words, plus the
lede, up to about 1,000 characters, in their original order.
"""

from __future__ import annotations

import re

DEFAULT_BUDGET = 1000

_SENTENCE_RE = re.compile(r"(?<=[.!?])[\"')\]]?\s+(?=[\"'(\[]?[A-Z0-9])")
_DIGIT_RE = re.compile(r"\d")
_JUNK_RE = re.compile(
    r"subscribe|sign up|newsletter|cookie|all rights reserved|advertisement|"
    r"click here|read more|follow us|getty images|reuters/|ap photo|"
    r"this story has been|this article (was|has been)|contributed to this report|"
    r"\bsee why\b|\bcurated\b|\bin our \d*|\bour (free|latest|full)\b|\bfind out\b|"
    r"\bdiscover (how|why|what)\b|\bcapitalize on\b|\bdon't miss\b|"
    r"\bwarning signs?\b|\byour cue\b|\bdo your own\b|\bnot financial advice\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[a-z0-9]+")


def split_sentences(text: str) -> list[str]:
    out = []
    for para in (text or "").split("\n"):
        for sentence in _SENTENCE_RE.split(para.strip()):
            sentence = sentence.strip()
            if sentence:
                out.append(sentence)
    return out


def build_factsheet(
    *,
    title: str,
    article: str | None = None,
    summaries: list[str] | None = None,
    entities: set[str] | list[str] | None = None,
    budget: int = DEFAULT_BUDGET,
) -> str:
    """A compact, fact-dense digest of the story for the writer.

    ``article`` is the fetched full text (may be ``None``); ``summaries`` are
    feed blurbs from the story's outlets, used as the lede and as a fallback
    when the article couldn't be fetched.
    """
    from opensocial.core.knowledge import FIGURE_PREFIX, title_tokens

    head = title_tokens(title)
    names = {e for e in (entities or []) if not e.startswith(FIGURE_PREFIX)}
    seen: set[str] = set()
    picked: list[tuple[int, str]] = []  # (original position, sentence)
    used = 0

    def add(pos: int, sentence: str) -> bool:
        nonlocal used
        key = sentence.lower()[:80]
        if key in seen or used + len(sentence) > budget:
            return False
        seen.add(key)
        picked.append((pos, sentence))
        used += len(sentence) + 1
        return True

    # Feed blurbs first: written to summarize, usually the best lede.
    for i, blurb in enumerate(summaries or []):
        blurb = " ".join((blurb or "").split())
        if blurb and len(blurb) >= 30:
            add(-100 + i, blurb[:400])
        if used >= budget * 0.4:
            break

    if article:
        sentences = [s for s in split_sentences(article) if 25 <= len(s) <= 400]
        scored = []
        for pos, sentence in enumerate(sentences):
            if _JUNK_RE.search(sentence):
                continue
            words = set(_WORD_RE.findall(sentence.lower()))
            score = 0.0
            score += 2.0 if _DIGIT_RE.search(sentence) else 0.0
            score += 1.5 * len(names & words)
            score += 0.6 * len(head & words)
            score += 1.0 if pos < 4 else 0.0  # the lede carries the news
            if '"' in sentence or "“" in sentence:
                score += 0.5  # quotes give the post something concrete to react to
            scored.append((score, pos, sentence))
        scored.sort(key=lambda t: (-t[0], t[1]))
        for score, pos, sentence in scored:
            if score <= 0:
                break
            add(pos, sentence)

    picked.sort(key=lambda t: t[0])
    return "\n".join(f"- {s}" for _, s in picked)
