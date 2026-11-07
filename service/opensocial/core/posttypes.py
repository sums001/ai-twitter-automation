"""Tone taxonomy: config parsing and shuffled-rotation selection.

Each niche's ``post_types`` block enables a subset of the seven **tones**, the
shape a post is written in (``news`` = a fact plus a reaction, ``take``, a dry
``meme`` one-liner, …) so a feed doesn't all sound alike. The per-tone shape text
lives in :data:`opensocial.ai.prompts.POST_TYPE_TEMPLATES`; the weighted choice
(never repeating the account's previous tone) is
:func:`opensocial.core.generate.choose_tone`.
"""

from __future__ import annotations

import random as _random
from dataclasses import dataclass

# Canonical tone set / order. Keys match POST_TYPE_TEMPLATES in ai/prompts.py.
ALL_TONES: list[str] = [
    "news", "spotlight", "insight", "take", "tip", "question", "meme",
]


@dataclass
class PostTypeRule:
    enabled: bool = True


@dataclass
class PostTypesConfig:
    rules: dict[str, PostTypeRule]

    @classmethod
    def from_niche(cls, raw: dict) -> "PostTypesConfig":
        block = (raw or {}).get("post_types") or {}
        rules: dict[str, PostTypeRule] = {}
        for ptype, spec in block.items():
            spec = spec or {}
            rules[ptype] = PostTypeRule(enabled=bool(spec.get("enabled", True)))
        return cls(rules=rules)

    def enabled_tones(self) -> list[str]:
        """Enabled tones in canonical order.

        With a ``post_types`` block, a tone is active only if it's declared and
        enabled. Falls back to :data:`ALL_TONES` when the niche enables none (or
        declares no block at all) so generation never stalls for lack of a tone —
        a bare niche simply gets the full, varied set.
        """
        if not self.rules:
            return list(ALL_TONES)
        enabled = [
            t for t in ALL_TONES
            if t in self.rules and self.rules[t].enabled
        ]
        return enabled or list(ALL_TONES)


def shuffled_deck(tones: list[str], rng: _random.Random | None = None) -> list[str]:
    """A reshuffled copy of ``tones`` to deal one-per-draft (pop from the end).

    The caller pops a tone per draft and refills with a fresh deck when empty,
    giving a rotation that exhausts every enabled tone before repeating.
    """
    deck = list(tones) or list(ALL_TONES)
    (rng or _random).shuffle(deck)
    return deck
