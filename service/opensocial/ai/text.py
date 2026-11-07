"""Text generation providers behind one tiny interface.

``TextProvider.generate(system, user)`` returns a single block of post text.
Two implementations ship:

* :class:`LiteLLMProvider` — the real one. LiteLLM gives one call shape across
  OpenAI/Anthropic/Gemini/Ollama; the default model is a local Ollama model so
  no API key is needed. ``litellm`` is imported lazily so the package (and the
  test suite) load fine when it isn't installed.
* :class:`TemplateProvider` — an offline, deterministic provider that echoes the
  prompt subject without any model. It is used **only when explicitly selected**
  (``provider: template``) for offline runs and tests — generation never falls
  back to it on error.

``get_text_provider(config)`` picks one from a niche's ``ai.text`` block. A real
provider that can't be built (LiteLLM missing) raises :class:`TextProviderError`
rather than silently degrading to the template provider.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod


class TextProvider(ABC):
    name: str

    @abstractmethod
    def generate(self, system: str, user: str) -> str:
        """Return generated text for the given system + user prompt."""
        raise NotImplementedError


class LiteLLMProvider(TextProvider):
    """Real provider via LiteLLM.

    One call shape spans Claude / ChatGPT / a local OpenAI-compatible or Ollama
    server. ``api_base`` points at a custom or local endpoint; API keys are read
    from the environment (``OPENAI_API_KEY`` / ``ANTHROPIC_API_KEY``) that the
    credentials store injects, so they never have to be passed in here.
    """

    def __init__(
        self,
        model: str = "ollama/gemma3:4b",
        api_base: str | None = None,
        api_key: str | None = None,
        **params,
    ) -> None:
        self.model = model
        self.api_base = (api_base or "").strip() or None
        # Explicit per-workspace key; falls back to the process env when None so
        # two workspaces can authenticate with different keys in one process.
        self.api_key = (api_key or "").strip() or None
        self.params = params
        self.name = model

    def generate(self, system: str, user: str) -> str:
        import litellm  # lazy: optional dependency

        kwargs = dict(self.params)
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.api_key:
            kwargs["api_key"] = self.api_key
        resp = litellm.completion(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **kwargs,
        )
        return resp["choices"][0]["message"]["content"] or ""


class TemplateProvider(TextProvider):
    """Deterministic, model-free provider used offline and in tests.

    It produces a plausible standalone post from the prompt's subject line and
    obeys a ``shorten to N characters`` instruction in the system prompt (the
    rewrite-to-fit pass), so the generator's post-processing can be exercised
    without a real model.
    """

    name = "template"

    _SUBJECT_RE = re.compile(r"SUBJECT:\s*(.+)", re.IGNORECASE)
    _SHORTEN_RE = re.compile(r"shorten[^0-9]*(\d+)\s*characters", re.IGNORECASE)

    def generate(self, system: str, user: str) -> str:
        subject = ""
        m = self._SUBJECT_RE.search(user)
        if m:
            subject = m.group(1).strip()
        text = subject or user.strip().splitlines()[0] if user.strip() else "Untitled"

        shorten = self._SHORTEN_RE.search(system) or self._SHORTEN_RE.search(user)
        if shorten:
            limit = int(shorten.group(1))
            if len(text) > limit:
                text = text[: max(0, limit - 1)].rstrip() + "…"
        return text


# Friendly provider name → default LiteLLM model. The user can still override
# ``model``; this only supplies a sensible default per provider.
PROVIDER_MODEL_DEFAULTS = {
    "claude": "anthropic/claude-sonnet-4-6",
    "chatgpt": "gpt-4o-mini",
    "openai": "gpt-4o-mini",
    "local": "ollama/gemma3:4b",
    "litellm": "ollama/gemma3:4b",  # legacy provider name
}

# Which credential a text provider authenticates with. ``local`` / ``template``
# need none. Used to resolve the right per-workspace key for generation.
PROVIDER_KEY_ENV = {
    "claude": "ANTHROPIC_API_KEY",
    "chatgpt": "OPENAI_API_KEY",
    "openai": "OPENAI_API_KEY",
}


class TextProviderError(RuntimeError):
    """Raised when a configured real provider can't be built or used.

    Generation deliberately does **not** fall back to the offline template
    provider on error — a misconfigured or failing model should surface as a
    loud, logged failure rather than silently emit raw, title-echoing drafts.
    """


def get_text_provider(config: dict | None) -> TextProvider:
    """Build the text provider from a niche's ``ai.text`` config.

    ``provider: template`` yields the offline :class:`TemplateProvider` (an
    explicit, deliberate choice for tests/offline). Any other provider
    (``claude`` / ``chatgpt`` / ``local``) routes through
    :class:`LiteLLMProvider`, using a per-provider default model when none is set
    and the configured ``endpoint`` as the API base (required for ``local``,
    optional elsewhere). If LiteLLM isn't installed this **raises** rather than
    falling back to the template provider — no silent degradation.
    """
    ai = ((config or {}).get("ai") or {}).get("text") or {}
    provider = (ai.get("provider") or "local").lower()

    if provider == "template":
        return TemplateProvider()

    model = ai.get("model") or PROVIDER_MODEL_DEFAULTS.get(provider, "ollama/llama3")
    endpoint = ai.get("endpoint") or None
    api_key = ai.get("api_key") or None  # per-workspace key injected by callers
    if provider == "local" and not api_key:
        # OpenAI-compatible local servers (``openai/<model>``) still make the
        # OpenAI client demand a key; they ignore its value. Ollama ignores it too.
        api_key = "local"
    params = {k: v for k, v in ai.items() if k in ("temperature", "max_tokens")}
    try:
        import litellm  # noqa: F401
    except Exception as exc:
        raise TextProviderError(
            f"AI text provider {provider!r} requires litellm, which isn't "
            "installed. Install it (pip install litellm) and restart — refusing "
            "to fall back to the offline template provider."
        ) from exc
    return LiteLLMProvider(model=model, api_base=endpoint, api_key=api_key, **params)
