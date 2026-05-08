"""LLM provider factory.

OpenKite is provider-agnostic: pick any chat model that supports tool calling.
Selection is configured via two env vars:

    OPENKITE_PROVIDER   anthropic | openai | google | mistral | groq |
                        qwen | ollama | openrouter   (default: anthropic)
    OPENKITE_MODEL      provider-specific model id, or combined form
                        ``"provider:model"`` (e.g. ``openai:gpt-4o``).

Each provider needs its own API key env var (see ``PROVIDERS`` below) and its
optional pip extra installed, e.g. ``pip install cloudops-openkite[openai]``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

DEFAULT_PROVIDER = "anthropic"


@dataclass(frozen=True)
class Provider:
    """Static config for a chat-model provider."""

    name: str
    default_model: str
    env_key: str | None  # API key env var, or None for local providers
    extra: str  # pip extra: ``cloudops-openkite[<extra>]``
    via_openai: bool = False  # route through ChatOpenAI(base_url=...)
    base_url: str | None = None


PROVIDERS: dict[str, Provider] = {
    "anthropic": Provider("anthropic", "claude-haiku-4-5-20251001", "ANTHROPIC_API_KEY", "anthropic"),
    "openai":    Provider("openai", "gpt-4o-mini", "OPENAI_API_KEY", "openai"),
    "google":    Provider("google", "gemini-2.0-flash", "GOOGLE_API_KEY", "google"),
    "mistral":   Provider("mistral", "mistral-large-latest", "MISTRAL_API_KEY", "mistral"),
    "groq":      Provider("groq", "llama-3.3-70b-versatile", "GROQ_API_KEY", "groq"),
    "qwen": Provider(
        "qwen",
        "qwen3-coder-plus",
        "DASHSCOPE_API_KEY",
        "qwen",
        via_openai=True,
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
    ),
    "ollama": Provider("ollama", "llama3.1", None, "ollama"),
    "openrouter": Provider(
        "openrouter",
        "anthropic/claude-haiku-4.5",
        "OPENROUTER_API_KEY",
        "openai",
        via_openai=True,
        base_url="https://openrouter.ai/api/v1",
    ),
}


def _resolve(provider: str | None, model: str | None) -> tuple[Provider, str]:
    """Resolve provider+model from args, env vars, and defaults."""
    raw_model = model or os.getenv("OPENKITE_MODEL")
    if raw_model and ":" in raw_model and raw_model.split(":", 1)[0] in PROVIDERS:
        # Combined form ``provider:model`` overrides everything.
        prov_name, raw_model = raw_model.split(":", 1)
    else:
        prov_name = provider or os.getenv("OPENKITE_PROVIDER", DEFAULT_PROVIDER)

    if prov_name not in PROVIDERS:
        supported = ", ".join(PROVIDERS)
        raise ValueError(f"Unknown provider {prov_name!r}. Supported: {supported}.")

    prov = PROVIDERS[prov_name]
    return prov, raw_model or prov.default_model


def _import_error(prov: Provider, exc: ImportError) -> ImportError:
    return ImportError(
        f"{prov.name} provider needs an extra package. Install it with:\n"
        f"    pip install 'cloudops-openkite[{prov.extra}]'\n"
        f"(original error: {exc})"
    )


def build_llm(
    provider: str | None = None,
    model: str | None = None,
    temperature: float = 0,
    max_tokens: int = 4096,
) -> Any:
    """Return a tool-calling chat model for the resolved provider+model.

    Args:
        provider: Provider name. Falls back to ``$OPENKITE_PROVIDER``, then ``anthropic``.
        model: Model id. Accepts the combined ``provider:model`` form. Falls
            back to ``$OPENKITE_MODEL``, then the provider default.

    Raises:
        EnvironmentError: when the provider's API key env var is missing.
        ImportError: when the provider's optional extra is not installed.
        ValueError: when the provider name is not recognised.
    """
    prov, model_id = _resolve(provider, model)

    if prov.env_key and not os.getenv(prov.env_key):
        raise OSError(
            f"{prov.name} provider requires the {prov.env_key} environment variable. "
            f"Export it and retry."
        )

    common = {"temperature": temperature, "max_tokens": max_tokens}

    if prov.via_openai:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:  # pragma: no cover - import-time guard
            raise _import_error(prov, exc) from exc
        return ChatOpenAI(
            model=model_id,
            base_url=prov.base_url,
            api_key=os.getenv(prov.env_key) if prov.env_key else None,
            **common,
        )

    try:
        from langchain.chat_models import init_chat_model
    except ImportError as exc:  # pragma: no cover - langchain is a hard dep
        raise ImportError(
            "langchain is required. Install with: pip install langchain>=0.3"
        ) from exc

    try:
        return init_chat_model(f"{prov.name}:{model_id}", **common)
    except ImportError as exc:
        raise _import_error(prov, exc) from exc
