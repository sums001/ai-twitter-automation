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

import functools
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


# ---------------------------------------------------------------------------
# Provider registry: the single source of truth for every selectable text
# provider. The dashboard reads it (``GET /api/ai/providers``) to render the
# provider picker, so adding a provider means adding one entry here.
#
# ``prefix`` is the LiteLLM route; a model typed without one ("gpt-4o-mini")
# gets it prepended (see ``qualify_model``). ``key_env`` is the per-workspace
# credential the provider authenticates with; ``key_required`` False means the
# key is optional (local servers that may or may not check one). ``models`` are
# suggestions only: any model id the provider accepts can be typed.
# ---------------------------------------------------------------------------
PROVIDERS: dict[str, dict] = {
    "claude": {
        "label": "Anthropic",
        "tagline": "Claude models",
        "prefix": "anthropic/",
        "key_env": "ANTHROPIC_API_KEY",
        "key_required": True,
        "key_url": "https://console.anthropic.com/settings/keys",
        "default_model": "anthropic/claude-sonnet-5-5",
        "models": [
            "anthropic/claude-sonnet-5-5",
            "anthropic/claude-haiku-5-5",
            "anthropic/claude-opus-5-5",
        ],
    },
    "chatgpt": {
        "label": "OpenAI",
        "tagline": "GPT models",
        "prefix": "openai/",
        "key_env": "OPENAI_API_KEY",
        "key_required": True,
        "key_url": "https://platform.openai.com/api-keys",
        "default_model": "gpt-4o-mini",
        "models": ["gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1", "gpt-4o"],
    },
    "gemini": {
        "label": "Google Gemini",
        "tagline": "Gemini via AI Studio",
        "prefix": "gemini/",
        "key_env": "GEMINI_API_KEY",
        "key_required": True,
        "key_url": "https://aistudio.google.com/apikey",
        "default_model": "gemini/gemini-2.5-flash",
        "models": ["gemini/gemini-2.5-flash", "gemini/gemini-2.5-pro"],
    },
    "openrouter": {
        "label": "OpenRouter",
        "tagline": "many models, one key",
        "prefix": "openrouter/",
        "key_env": "OPENROUTER_API_KEY",
        "key_required": True,
        "key_url": "https://openrouter.ai/keys",
        "default_model": "openrouter/meta-llama/llama-3.3-70b-instruct",
        "models": [
            "openrouter/meta-llama/llama-3.3-70b-instruct",
            "openrouter/google/gemini-2.5-flash",
            "openrouter/deepseek/deepseek-chat",
        ],
    },
    "groq": {
        "label": "Groq",
        "tagline": "fast open models",
        "prefix": "groq/",
        "key_env": "GROQ_API_KEY",
        "key_required": True,
        "key_url": "https://console.groq.com/keys",
        "default_model": "groq/llama-3.3-70b-versatile",
        "models": ["groq/llama-3.3-70b-versatile", "groq/llama-3.1-8b-instant"],
    },
    "mistral": {
        "label": "Mistral",
        "tagline": "La Plateforme",
        "prefix": "mistral/",
        "key_env": "MISTRAL_API_KEY",
        "key_required": True,
        "key_url": "https://console.mistral.ai/api-keys",
        "default_model": "mistral/mistral-small-latest",
        "models": ["mistral/mistral-small-latest", "mistral/mistral-large-latest"],
    },
    "deepseek": {
        "label": "DeepSeek",
        "tagline": "DeepSeek platform",
        "prefix": "deepseek/",
        "key_env": "DEEPSEEK_API_KEY",
        "key_required": True,
        "key_url": "https://platform.deepseek.com/api_keys",
        "default_model": "deepseek/deepseek-chat",
        "models": ["deepseek/deepseek-chat"],
    },
    "local": {
        "label": "Local server",
        "tagline": "Ollama, LM Studio, any OpenAI-compatible API",
        "prefix": "",  # the model carries its own route: ollama/... or openai/...
        "key_env": "LOCAL_LLM_API_KEY",
        "key_required": False,
        "key_url": "",
        "default_model": "ollama/gemma3:4b",
        "default_endpoint": "http://localhost:11434",
        "endpoint_required": True,
        "models": ["ollama/gemma3:4b", "ollama/llama3.2", "ollama/qwen2.5"],
        "presets": [
            {"label": "Ollama", "endpoint": "http://localhost:11434", "model": "ollama/gemma3:4b"},
            {"label": "LM Studio", "endpoint": "http://localhost:1234/v1", "model": "openai/local-model"},
            {"label": "llama.cpp", "endpoint": "http://localhost:8080/v1", "model": "openai/local-model"},
            {"label": "vLLM", "endpoint": "http://localhost:8000/v1", "model": "openai/served-model"},
        ],
    },
    "cloudflare": {
        "label": "Cloudflare",
        "tagline": "Workers AI, open models on your account",
        # Workers AI's OpenAI-compatible API (its native /ai/run/<model> API
        # takes a different request shape). Model ids look like
        # @cf/vendor/model, so they always get the openai/ route.
        "prefix": "openai/",
        "passthrough_routes": True,
        "key_env": "CLOUDFLARE_API_TOKEN",
        "key_required": True,
        "key_url": "https://dash.cloudflare.com/profile/api-tokens",
        "default_model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "models": [
            "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
            "@cf/qwen/qwen3.8-27b",
            "@cf/openai/gpt-oss-20b",
            "@cf/mistralai/mistral-small-3.1-24b-instruct",
            "@cf/google/gemma-4-26b-a4b-it",
        ],
        # The endpoint is built from the account id; a custom base URL (e.g. an
        # AI Gateway) under Advanced overrides it.
        "endpoint_template": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1",
        "fields": [
            {
                "key": "account_id",
                "label": "Account ID",
                "hint": "on your Cloudflare dashboard's overview page",
                "placeholder": "32-character account id",
                "required": True,
            },
        ],
    },
    "custom": {
        "label": "Custom",
        "tagline": "any OpenAI-compatible API: your endpoint, model and key",
        # Bare ids (and vendor/model ids such as meta-llama/...) go out over the
        # OpenAI-compatible route; an id that already names a LiteLLM route
        # (together_ai/..., azure/...) is kept as is. See ``qualify_model``.
        "prefix": "openai/",
        "passthrough_routes": True,
        "key_env": "CUSTOM_LLM_API_KEY",
        "key_required": False,
        "key_url": "",
        "default_model": "",
        "default_endpoint": "",
        "endpoint_placeholder": "https://api.example.com/v1",
        "endpoint_required": True,
        "model_placeholder": "the model id your endpoint expects",
        "model_required": True,
        "models": [],
    },
    "template": {
        "label": "Offline template",
        "tagline": "no model, no key, no network",
        "prefix": "",
        "key_env": None,
        "key_required": False,
        "key_url": "",
        "default_model": "",
        "models": [],
    },
}

# Legacy provider names still accepted in stored configs.
_ALIASES = {"openai": "chatgpt", "anthropic": "claude", "litellm": "local"}


def canonical_provider(name: str | None) -> str:
    """A stored provider name → its registry key (legacy aliases resolved)."""
    name = (name or "local").strip().lower()
    return _ALIASES.get(name, name)


# Friendly provider name → default LiteLLM model (derived from the registry).
PROVIDER_MODEL_DEFAULTS = {
    **{k: v["default_model"] for k, v in PROVIDERS.items() if v["default_model"]},
    "openai": PROVIDERS["chatgpt"]["default_model"],
    "litellm": PROVIDERS["local"]["default_model"],
}

# Which credential a text provider authenticates with (derived from the
# registry). ``local`` is listed too: its key is optional, but one the user set
# is passed through for servers that check it.
PROVIDER_KEY_ENV = {
    **{k: v["key_env"] for k, v in PROVIDERS.items() if v["key_env"]},
    "openai": PROVIDERS["chatgpt"]["key_env"],
}

# Every credential any text provider can use, in registry order.
AI_KEY_ENVS: tuple[str, ...] = tuple(
    dict.fromkeys(v["key_env"] for v in PROVIDERS.values() if v["key_env"])
)


def key_required(provider: str | None) -> bool:
    """Whether generation with this provider fails without an API key."""
    return bool(PROVIDERS.get(canonical_provider(provider), {}).get("key_required"))


@functools.lru_cache(maxsize=1)
def _litellm_routes() -> frozenset[str]:
    """LiteLLM's provider route names (``openai``, ``together_ai``, …)."""
    try:
        import litellm

        return frozenset(str(getattr(p, "value", p)) for p in litellm.provider_list)
    except Exception:
        return frozenset({"openai"})


def model_required(provider: str | None) -> bool:
    """Whether the provider has no default model, so one must be typed."""
    return bool(PROVIDERS.get(canonical_provider(provider), {}).get("model_required"))


def clean_field(key: str, value) -> str:
    """Normalise a provider field as typed. An ``account_id`` pasted as a whole
    dashboard or API URL is cut down to the id after ``/accounts/``."""
    value = str(value or "").strip()
    if key == "account_id" and "/accounts/" in value:
        value = value.split("/accounts/", 1)[1].split("/", 1)[0]
    return value


def missing_fields(provider: str | None, ai: dict) -> list[str]:
    """Labels of the provider's required extra fields left empty in ``ai``."""
    spec = PROVIDERS.get(canonical_provider(provider)) or {}
    return [
        f["label"] for f in spec.get("fields", [])
        if f.get("required") and not clean_field(f["key"], ai.get(f["key"]))
    ]


def resolve_endpoint(provider: str | None, ai: dict) -> str | None:
    """The API base URL: the configured ``endpoint``, else the provider's
    ``endpoint_template`` filled from its fields, else None (provider default)."""
    endpoint = (ai.get("endpoint") or "").strip()
    if endpoint:
        return endpoint
    spec = PROVIDERS.get(canonical_provider(provider)) or {}
    template = spec.get("endpoint_template")
    if not template or missing_fields(provider, ai):
        return None
    return template.format(**{
        f["key"]: clean_field(f["key"], ai.get(f["key"])) for f in spec.get("fields", [])
    })


def qualify_model(provider: str | None, model: str) -> str:
    """Prepend the provider's LiteLLM route to a bare model id.

    ``claude`` + ``claude-haiku-5-5`` → ``anthropic/claude-haiku-5-5``. A model
    that already carries the route is left alone. OpenRouter ids look like
    ``vendor/model``, so for it the route is added unless already present.
    """
    spec = PROVIDERS.get(canonical_provider(provider)) or {}
    prefix = spec.get("prefix") or ""
    model = (model or "").strip()
    if not prefix or not model or model.startswith(prefix):
        return model
    if spec.get("passthrough_routes"):
        head = model.split("/", 1)[0]
        return model if "/" in model and head in _litellm_routes() else prefix + model
    if prefix == "openrouter/" or "/" not in model:
        return prefix + model
    return model


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
    provider = canonical_provider(ai.get("provider"))

    if provider == "template":
        return TemplateProvider()

    if model_required(provider) and not (ai.get("model") or "").strip():
        raise TextProviderError(f"AI text provider {provider!r} needs a model id")
    missing = missing_fields(provider, ai)
    if missing:
        raise TextProviderError(f"AI text provider {provider!r} needs: {', '.join(missing)}")
    model = qualify_model(
        provider, ai.get("model") or PROVIDER_MODEL_DEFAULTS.get(provider, "ollama/llama3")
    )
    endpoint = resolve_endpoint(provider, ai)
    api_key = ai.get("api_key") or None  # per-workspace key injected by callers
    if not api_key and provider in PROVIDERS and not key_required(provider):
        # Keyless OpenAI-compatible servers (local or custom) still make the
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
