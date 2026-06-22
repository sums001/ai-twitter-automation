"""LLM semantic passes over the candidate queue (Phase 3 add-on).

Two passes live here, both layered on top of the deterministic
:func:`~opensocial.core.filtering.candidate_queue`:

* :func:`rerank_by_importance` — the queue blends recency / relevance /
  sentiment but has no sense of *significance*; this hands the top-K to the model
  in **one call** and reorders them by importance.
* :func:`choose_post_type` — picks the post **tone** that best fits an item, so
  the tag matches the content instead of a random rotation slot.

Both are no-ops without a real model — the offline :class:`TemplateProvider`
(and a missing LiteLLM) fall straight back to the deterministic behavior, so
tests and offline runs stay deterministic. Any parse/transport failure also
falls back, so a bad model response never drops candidates or mis-tags a draft.
"""

from __future__ import annotations

import re

from opensocial.ai.prompts import (
    build_image_query_messages,
    build_posttype_messages,
    build_ranking_messages,
)
from opensocial.ai.text import TextProvider

DEFAULT_TOP_K = 12
_INT_RE = re.compile(r"\d+")
# A short, comparable label per candidate for the ranking prompt.
_SUMMARY_CHARS = 200
# Keep only word characters when cleaning an LLM-written image query, and cap its
# length so a chatty model reply can't become a long, no-match Unsplash search.
_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")
_MAX_QUERY_WORDS = 4


def _is_offline(provider: TextProvider) -> bool:
    """The template provider can't reason about importance — skip it."""
    return getattr(provider, "name", "") == "template"


def _item_label(candidate) -> str:
    row = candidate.row
    summary = (row.summary or row.body or "").strip().replace("\n", " ")
    if summary:
        return f"{row.title} — {summary[:_SUMMARY_CHARS]}"
    return row.title


def _parse_order(reply: str, n: int) -> list[int]:
    """Parse a model reply into 0-based indices within ``range(n)``.

    Pulls integers out of the reply (1-based, as prompted), converts to 0-based,
    drops out-of-range and duplicate values. Returns ``[]`` on no usable ints.
    """
    seen: list[int] = []
    for match in _INT_RE.findall(reply or ""):
        idx = int(match) - 1
        if 0 <= idx < n and idx not in seen:
            seen.append(idx)
    return seen


def rerank_by_importance(
    candidates: list,
    config: dict,
    *,
    text_provider: TextProvider,
    niche_name: str | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> list:
    """Reorder ``candidates`` (best-first) by LLM-judged importance.

    Only the top ``top_k`` are sent to the model; the tail keeps its
    deterministic order and is appended unchanged. Any candidate the model
    omits from its ranking is appended in its original deterministic position,
    so the result is always a full permutation of the input.
    """
    if len(candidates) <= 1 or _is_offline(text_provider):
        return candidates

    head = candidates[:top_k]
    tail = candidates[top_k:]

    name = niche_name or (config or {}).get("display_name") or "this niche"
    system, user = build_ranking_messages(
        niche_name=name, items=[_item_label(c) for c in head]
    )
    try:
        reply = text_provider.generate(system, user)
    except Exception:
        # A model/transport failure must never sink generation — keep order.
        return candidates

    order = _parse_order(reply, len(head))
    if not order:
        return candidates

    ranked = [head[i] for i in order]
    # Append any head items the model didn't mention, in their original order.
    mentioned = set(order)
    ranked.extend(head[i] for i in range(len(head)) if i not in mentioned)
    ranked.extend(tail)
    return ranked


def _match_choice(reply: str, choices: list[str]) -> str | None:
    """Return the first enabled tone named in the reply, or ``None``."""
    text = (reply or "").lower()
    for choice in choices:
        if re.search(rf"\b{re.escape(choice.lower())}\b", text):
            return choice
    return None


def choose_post_type(
    candidate,
    config: dict,
    *,
    text_provider: TextProvider,
    choices: list[str],
    niche_name: str | None = None,
) -> str | None:
    """Pick the enabled tone that best fits ``candidate``, or ``None``.

    Returns ``None`` offline, when there's nothing to choose, or on any
    model/parse failure — the caller then falls back to its rotation deck, so a
    missing or misbehaving model never blocks generation.
    """
    if len(choices) <= 1 or _is_offline(text_provider):
        return None

    name = niche_name or (config or {}).get("display_name") or "this niche"
    row = candidate.row
    system, user = build_posttype_messages(
        niche_name=name,
        subject=row.title,
        body=(row.summary or row.body or ""),
        choices=choices,
    )
    try:
        reply = text_provider.generate(system, user)
    except Exception:
        return None
    return _match_choice(reply, choices)


def _clean_image_query(reply: str) -> str | None:
    """Reduce an LLM reply to a tight image query, or ``None`` if unusable.

    Takes the first non-empty line, keeps word tokens only (dropping quotes and
    punctuation a model might wrap the phrase in), and caps it at
    ``_MAX_QUERY_WORDS`` so a verbose reply can't become a long, no-match search.
    """
    for line in (reply or "").splitlines():
        words = _QUERY_TOKEN_RE.findall(line)
        if words:
            return " ".join(words[:_MAX_QUERY_WORDS])
    return None


def choose_image_query(
    config: dict,
    *,
    text_provider: TextProvider,
    niche_name: str,
    subject: str,
    body: str = "",
) -> str | None:
    """Ask the model for a concrete visual Unsplash query, or ``None``.

    Returns ``None`` offline or on any model/parse failure — the caller then
    falls back to the deterministic :func:`~opensocial.ai.prompts.unsplash_query`
    heuristic, so a missing or misbehaving model never blocks image attachment.
    """
    if _is_offline(text_provider):
        return None

    name = niche_name or (config or {}).get("display_name") or "this niche"
    system, user = build_image_query_messages(
        niche_name=name, subject=subject, body=body
    )
    try:
        reply = text_provider.generate(system, user)
    except Exception:
        return None
    return _clean_image_query(reply)
